from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .data_loader import Contractor, load_contractors
from .models import RecommendationCard, RecommendationRequest, RecommendationResponse


class ContractorRecommender:
    """Deterministic hybrid recommender: hard filters + transparent scoring + TF-IDF."""

    def __init__(self, csv_path: str | Path):
        self.contractors = load_contractors(csv_path)
        descriptions = [c.description for c in self.contractors]
        self.vectorizer = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=1,
            max_features=6000,
            token_pattern=r"(?u)\b[\w-]{3,}\b",
        )
        self.description_matrix = self.vectorizer.fit_transform(descriptions)
        self._index_by_id = {c.id: i for i, c in enumerate(self.contractors)}

    @staticmethod
    def _money(value: int) -> str:
        return f"{value:,}".replace(",", " ") + " ₸"

    @staticmethod
    def _hours(value: float) -> str:
        return f"{int(value) if value.is_integer() else value:g} ч"

    @staticmethod
    def _normalize(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    def metadata(self) -> dict:
        categories = sorted({x for c in self.contractors for x in c.categories})
        cities = sorted({c.city for c in self.contractors})
        formats = sorted({x for c in self.contractors for x in c.event_formats})
        languages = sorted({x for c in self.contractors for x in c.languages})
        all_busy_dates = [d for c in self.contractors for d in c.busy_dates]
        return {
            "cities": cities,
            "categories": categories,
            "event_formats": formats,
            "languages": languages,
            "date_min": min(all_busy_dates).isoformat(),
            "date_max": max(all_busy_dates).isoformat(),
            "profiles_count": len(self.contractors),
        }

    def _semantic_info(self, contractor: Contractor, preferences: str | None) -> tuple[float, list[str]]:
        preferences = self._normalize(preferences)
        if not preferences:
            return 0.0, []

        q = self.vectorizer.transform([preferences])
        d = self.description_matrix[self._index_by_id[contractor.id]]
        similarity = float(cosine_similarity(q, d)[0, 0])

        product = q.multiply(d).tocoo()
        if product.nnz == 0:
            return similarity, []

        features = self.vectorizer.get_feature_names_out()
        ranked = sorted(zip(product.col, product.data), key=lambda item: (-item[1], features[item[0]]))
        terms: list[str] = []
        for idx, _weight in ranked:
            term = features[idx]
            if term not in terms and len(term) >= 3:
                terms.append(term)
            if len(terms) == 3:
                break
        return similarity, terms

    def _hard_failures(self, c: Contractor, req: RecommendationRequest) -> list[str]:
        failures: list[str] = []
        if req.event_date in c.busy_dates:
            failures.append("busy")
        if req.event_format not in c.event_formats:
            failures.append("format")
        if c.price_from_kzt > req.budget_kzt:
            failures.append("budget")

        language = self._normalize(req.language)
        if language and language not in {x.lower() for x in c.languages}:
            failures.append("language")

        if req.duration_hours is not None and c.max_hours is not None and c.max_hours < req.duration_hours:
            failures.append("duration")
        return failures

    def _score(self, c: Contractor, req: RecommendationRequest) -> tuple[float, float, list[str]]:
        # Every candidate reaching this method already passed all hard constraints.
        # Score only decides the deterministic order among valid candidates.
        budget_ratio = c.price_from_kzt / req.budget_kzt
        budget_score = 25.0 * max(0.0, 1.0 - budget_ratio)
        format_score = 15.0
        language_score = 0.0
        duration_score = 0.0

        language = self._normalize(req.language)
        if language:
            language_score = 15.0

        if req.duration_hours is not None:
            if c.max_hours is None:
                duration_score = 5.0
            else:
                reserve = max(0.0, c.max_hours - req.duration_hours)
                duration_score = 10.0 + min(10.0, 10.0 * reserve / max(req.duration_hours, 1.0))

        semantic_similarity, semantic_terms = self._semantic_info(c, req.preferences)
        semantic_score = 25.0 * semantic_similarity

        score = budget_score + format_score + language_score + duration_score + semantic_score
        return score, semantic_similarity, semantic_terms

    def _reasons(
        self,
        c: Contractor,
        req: RecommendationRequest,
        semantic_similarity: float,
        semantic_terms: list[str],
    ) -> list[str]:
        reasons: list[str] = []
        date_text = req.event_date.strftime("%d.%m.%Y")
        reasons.append(
            f"Свободен {date_text}, работает в городе {c.city} и принимает формат «{req.event_format}»."
        )

        reserve = req.budget_kzt - c.price_from_kzt
        price_note = f"Цена от {self._money(c.price_from_kzt)} укладывается в бюджет"
        if reserve > 0:
            price_note += f"; запас — {self._money(reserve)}"
        price_note += "."

        details: list[str] = []
        language = self._normalize(req.language)
        if language:
            matching_language = next((x for x in c.languages if x.lower() == language), req.language or "")
            details.append(f"работает на языке «{matching_language}»")

        if req.duration_hours is not None:
            if c.max_hours is None:
                details.append("для этой услуги ограничение по часам не применяется")
            else:
                details.append(
                    f"может работать до {self._hours(c.max_hours)}, запрос — {self._hours(float(req.duration_hours))}"
                )

        if semantic_similarity > 0 and semantic_terms:
            details.append("в описании совпали ключевые признаки: " + ", ".join(f"«{t}»" for t in semantic_terms))

        if details:
            price_note += " Также " + "; ".join(details) + "."
        reasons.append(price_note)
        return reasons[:2]

    def recommend(self, req: RecommendationRequest) -> RecommendationResponse:
        # Stage 1: distinguish "there is no such category in this city" from filtering failures.
        city_category = [
            c for c in self.contractors if c.city == req.city and req.category in c.categories
        ]

        if not city_category:
            available_cities = sorted({c.city for c in self.contractors if req.category in c.categories})
            diagnostics = {
                "city_category_candidates": 0,
                "available_cities_for_category": available_cities,
            }
            city_hint = (
                " Категория есть в городах: " + ", ".join(available_cities) + "."
                if available_cities
                else " Такой категории нет в датасете."
            )
            return RecommendationResponse(
                status="category_absent",
                message=f"В городе {req.city} нет подрядчиков категории «{req.category}»." + city_hint,
                results=[],
                diagnostics=diagnostics,
            )

        failure_counts: Counter[str] = Counter()
        valid: list[Contractor] = []
        for contractor in city_category:
            failures = self._hard_failures(contractor, req)
            failure_counts.update(failures)
            if not failures:
                valid.append(contractor)

        if not valid:
            labels = {
                "busy": "заняты на выбранную дату",
                "format": "не работают с выбранным форматом",
                "budget": "выше бюджета",
                "language": "не поддерживают выбранный язык",
                "duration": "не подходят по длительности",
            }
            parts = [f"{count} — {labels[key]}" for key, count in sorted(failure_counts.items()) if count]
            message = "Кандидаты в этой категории есть, но ни один не проходит по условиям."
            if parts:
                message += " Причины среди кандидатов: " + "; ".join(parts) + "."
            return RecommendationResponse(
                status="no_match",
                message=message,
                results=[],
                diagnostics={
                    "city_category_candidates": len(city_category),
                    "valid_candidates": 0,
                    **{f"excluded_{k}": int(v) for k, v in failure_counts.items()},
                },
            )

        ranked: list[tuple[float, Contractor, float, list[str]]] = []
        for contractor in valid:
            score, semantic_similarity, semantic_terms = self._score(contractor, req)
            ranked.append((score, contractor, semantic_similarity, semantic_terms))

        # Stable, explicit tie-breakers make the same request return the same order.
        ranked.sort(key=lambda item: (-round(item[0], 8), item[1].price_from_kzt, item[1].id))

        cards: list[RecommendationCard] = []
        for score, c, semantic_similarity, semantic_terms in ranked[:3]:
            cards.append(
                RecommendationCard(
                    id=c.id,
                    name=c.anon_name,
                    categories=list(c.categories),
                    city=c.city,
                    price_from_kzt=c.price_from_kzt,
                    event_formats=list(c.event_formats),
                    languages=list(c.languages),
                    max_hours=c.max_hours,
                    score=round(score, 4),
                    reasons=self._reasons(c, req, semantic_similarity, semantic_terms),
                    description=c.description,
                    synthetic=c.synthetic,
                    city_imputed=c.city_imputed,
                    price_imputed=c.price_imputed,
                )
            )

        message = f"Подобрано {len(cards)} из {len(valid)} подходящих кандидатов."
        if len(cards) < 3:
            labels = {
                "busy": "заняты на выбранную дату",
                "format": "не работают с выбранным форматом",
                "budget": "выше бюджета",
                "language": "не поддерживают выбранный язык",
                "duration": "не подходят по длительности",
            }
            excluded_parts = [
                f"{count} — {labels[key]}"
                for key, count in sorted(failure_counts.items())
                if count
            ]
            message += " Подходящих подрядчиков меньше трёх после применения всех условий."
            if excluded_parts:
                message += " Среди остальных кандидатов: " + "; ".join(excluded_parts) + "."

        return RecommendationResponse(
            status="matched",
            message=message,
            results=cards,
            diagnostics={
                "city_category_candidates": len(city_category),
                "valid_candidates": len(valid),
                **{f"excluded_{k}": int(v) for k, v in failure_counts.items()},
            },
        )
