from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .data_loader import Contractor, load_contractors
from .models import PlanBOption, RecommendationCard, RecommendationRequest, RecommendationResponse


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
        all_busy_dates = [d for c in self.contractors for d in c.busy_dates]
        self.date_min = min(all_busy_dates)
        self.date_max = max(all_busy_dates)

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

    @staticmethod
    def _plural_candidates(count: int) -> str:
        if count % 10 == 1 and count % 100 != 11:
            word = "подходящий вариант"
        elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
            word = "подходящих варианта"
        else:
            word = "подходящих вариантов"
        return f"{count} {word}"

    def metadata(self) -> dict:
        categories = sorted({x for c in self.contractors for x in c.categories})
        cities = sorted({c.city for c in self.contractors})
        formats = sorted({x for c in self.contractors for x in c.event_formats})
        languages = sorted({x for c in self.contractors for x in c.languages})
        return {
            "cities": cities,
            "categories": categories,
            "event_formats": formats,
            "languages": languages,
            "date_min": self.date_min.isoformat(),
            "date_max": self.date_max.isoformat(),
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

    def _city_category_candidates(self, req: RecommendationRequest) -> list[Contractor]:
        return [
            c for c in self.contractors if c.city == req.city and req.category in c.categories
        ]

    def _valid_candidates(
        self,
        req: RecommendationRequest,
        candidates: list[Contractor] | None = None,
    ) -> list[Contractor]:
        pool = candidates if candidates is not None else self._city_category_candidates(req)
        return [c for c in pool if not self._hard_failures(c, req)]

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

    def _rank_valid(
        self,
        valid: list[Contractor],
        req: RecommendationRequest,
    ) -> list[tuple[float, Contractor, float, list[str]]]:
        ranked: list[tuple[float, Contractor, float, list[str]]] = []
        for contractor in valid:
            score, semantic_similarity, semantic_terms = self._score(contractor, req)
            ranked.append((score, contractor, semantic_similarity, semantic_terms))
        ranked.sort(key=lambda item: (-round(item[0], 8), item[1].price_from_kzt, item[1].id))
        return ranked

    def _preview_ids(self, req: RecommendationRequest, valid: list[Contractor]) -> list[str]:
        return [item[1].id for item in self._rank_valid(valid, req)[:3]]

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

        details: list[str] = []
        language = self._normalize(req.language)
        if language:
            matching_language = next((x for x in c.languages if x.lower() == language), req.language or "")
            details.append(f"язык — «{matching_language}»")

        if req.duration_hours is not None:
            if c.max_hours is None:
                details.append("ограничение по часам для услуги не применяется")
            else:
                details.append(
                    f"может работать до {self._hours(c.max_hours)} при запросе {self._hours(float(req.duration_hours))}"
                )

        if semantic_similarity > 0 and semantic_terms:
            details.append("в описании совпали признаки: " + ", ".join(f"«{t}»" for t in semantic_terms))

        if details:
            price_note += "; " + "; ".join(details)
        price_note += "."
        reasons.append(price_note)
        return reasons[:2]

    def _plan_b_date(
        self,
        req: RecommendationRequest,
        current_count: int,
    ) -> PlanBOption | None:
        max_distance = max(
            (req.event_date - self.date_min).days,
            (self.date_max - req.event_date).days,
        )

        for distance in range(1, max_distance + 1):
            candidates: list[tuple[date, list[Contractor], RecommendationRequest]] = []
            for target in (req.event_date + timedelta(days=distance), req.event_date - timedelta(days=distance)):
                if target < self.date_min or target > self.date_max:
                    continue
                alternative = req.model_copy(update={"event_date": target})
                valid = self._valid_candidates(alternative)
                if len(valid) > current_count:
                    candidates.append((target, valid, alternative))

            if not candidates:
                continue

            target, valid, alternative = sorted(
                candidates,
                key=lambda item: (-len(item[1]), item[0]),
            )[0]
            count = len(valid)
            return PlanBOption(
                kind="date",
                title=f"Сдвинуть дату на {target.strftime('%d.%m.%Y')}",
                description=(
                    f"Ближайшая дата с лучшей доступностью: {self._plural_candidates(count)} "
                    "при остальных условиях без изменений."
                ),
                candidate_count=count,
                gain=count - current_count,
                patch={"event_date": target.isoformat()},
                preview_ids=self._preview_ids(alternative, valid),
            )
        return None

    def _plan_b_budget(
        self,
        req: RecommendationRequest,
        current_count: int,
    ) -> PlanBOption | None:
        pool = self._city_category_candidates(req)
        thresholds = sorted({c.price_from_kzt for c in pool if c.price_from_kzt > req.budget_kzt})
        for budget in thresholds:
            alternative = req.model_copy(update={"budget_kzt": budget})
            valid = self._valid_candidates(alternative, pool)
            if len(valid) <= current_count:
                continue
            count = len(valid)
            delta = budget - req.budget_kzt
            return PlanBOption(
                kind="budget",
                title=f"Увеличить бюджет на {self._money(delta)}",
                description=(
                    f"При бюджете {self._money(budget)} доступно {self._plural_candidates(count)}; "
                    "остальные параметры сохраняются."
                ),
                candidate_count=count,
                gain=count - current_count,
                patch={"budget_kzt": budget},
                preview_ids=self._preview_ids(alternative, valid),
            )
        return None

    def _plan_b_language(
        self,
        req: RecommendationRequest,
        current_count: int,
    ) -> PlanBOption | None:
        language = self._normalize(req.language)
        if not language:
            return None
        alternative = req.model_copy(update={"language": None})
        valid = self._valid_candidates(alternative)
        if len(valid) <= current_count:
            return None
        count = len(valid)
        return PlanBOption(
            kind="language",
            title="Сделать язык необязательным",
            description=(
                f"Без обязательного языка доступно {self._plural_candidates(count)}. "
                f"Сейчас задано требование «{req.language}»."
            ),
            candidate_count=count,
            gain=count - current_count,
            patch={"language": None},
            preview_ids=self._preview_ids(alternative, valid),
        )

    def _plan_b_duration(
        self,
        req: RecommendationRequest,
        current_count: int,
    ) -> PlanBOption | None:
        if req.duration_hours is None:
            return None

        pool = self._city_category_candidates(req)
        thresholds = sorted(
            {
                c.max_hours
                for c in pool
                if c.max_hours is not None and c.max_hours < req.duration_hours
            },
            reverse=True,
        )
        for duration in thresholds:
            alternative = req.model_copy(update={"duration_hours": duration})
            valid = self._valid_candidates(alternative, pool)
            if len(valid) <= current_count:
                continue
            count = len(valid)
            reduction = float(req.duration_hours) - float(duration)
            return PlanBOption(
                kind="duration",
                title=f"Сократить длительность на {self._hours(reduction)}",
                description=(
                    f"При длительности {self._hours(float(duration))} доступно "
                    f"{self._plural_candidates(count)} при остальных условиях без изменений."
                ),
                candidate_count=count,
                gain=count - current_count,
                patch={"duration_hours": float(duration)},
                preview_ids=self._preview_ids(alternative, valid),
            )
        return None

    def _plan_b_date_budget(
        self,
        req: RecommendationRequest,
        current_count: int,
    ) -> PlanBOption | None:
        """Fallback for requests where availability and budget block each other.

        It searches the nearest alternative date and, for that date, the smallest budget
        increase that unlocks at least one additional valid candidate.
        """
        pool = self._city_category_candidates(req)
        thresholds = sorted({c.price_from_kzt for c in pool if c.price_from_kzt > req.budget_kzt})
        if not thresholds:
            return None

        max_distance = max(
            (req.event_date - self.date_min).days,
            (self.date_max - req.event_date).days,
        )
        for distance in range(1, max_distance + 1):
            choices: list[tuple[int, int, date, list[Contractor], RecommendationRequest]] = []
            for target in (req.event_date + timedelta(days=distance), req.event_date - timedelta(days=distance)):
                if target < self.date_min or target > self.date_max:
                    continue
                for budget in thresholds:
                    alternative = req.model_copy(update={"event_date": target, "budget_kzt": budget})
                    valid = self._valid_candidates(alternative, pool)
                    if len(valid) > current_count:
                        choices.append((budget - req.budget_kzt, -len(valid), target, valid, alternative))
                        break

            if not choices:
                continue

            delta, _negative_count, target, valid, alternative = sorted(
                choices,
                key=lambda item: (item[0], item[1], item[2]),
            )[0]
            count = len(valid)
            return PlanBOption(
                kind="combined",
                title="Сдвинуть дату и скорректировать бюджет",
                description=(
                    f"Дата {target.strftime('%d.%m.%Y')} и бюджет {self._money(alternative.budget_kzt)} "
                    f"дают {self._plural_candidates(count)}. Бюджет нужно увеличить на {self._money(delta)}."
                ),
                candidate_count=count,
                gain=count - current_count,
                patch={
                    "event_date": target.isoformat(),
                    "budget_kzt": alternative.budget_kzt,
                },
                preview_ids=self._preview_ids(alternative, valid),
            )
        return None

    def _plan_b_city_for_absent(self, req: RecommendationRequest) -> list[PlanBOption]:
        options: list[PlanBOption] = []
        cities = sorted({c.city for c in self.contractors if req.category in c.categories and c.city != req.city})
        for city in cities:
            alternative = req.model_copy(update={"city": city})
            category_pool = self._city_category_candidates(alternative)
            valid = self._valid_candidates(alternative, category_pool)
            count = len(valid)
            if count > 0:
                description = (
                    f"В городе {city} найдено {self._plural_candidates(count)} "
                    "с теми же датой, форматом, бюджетом и дополнительными условиями."
                )
            else:
                description = (
                    f"В городе {city} есть {len(category_pool)} профилей категории «{req.category}», "
                    "но текущие дополнительные условия могут потребовать корректировки."
                )
            options.append(
                PlanBOption(
                    kind="city",
                    title=f"Проверить город {city}",
                    description=description,
                    candidate_count=count,
                    gain=count,
                    patch={"city": city},
                    preview_ids=self._preview_ids(alternative, valid) if valid else [],
                )
            )

        options.sort(key=lambda option: (-option.candidate_count, option.title))
        return options[:2]

    def _build_plan_b(
        self,
        req: RecommendationRequest,
        current_count: int,
    ) -> list[PlanBOption]:
        if current_count >= 3:
            return []

        options = [
            self._plan_b_date(req, current_count),
            self._plan_b_budget(req, current_count),
            self._plan_b_language(req, current_count),
            self._plan_b_duration(req, current_count),
        ]
        available = [option for option in options if option is not None]

        # Some strict requests require two small changes at once: for example the nearest
        # free date may still be over budget, while a budget increase alone keeps everyone busy.
        if not available:
            combined = self._plan_b_date_budget(req, current_count)
            if combined is not None:
                available.append(combined)

        # Prefer changes that unlock more candidates. For equal impact keep a predictable,
        # human-friendly order and prefer a one-field change over a combined fallback.
        priority = {"date": 0, "budget": 1, "language": 2, "duration": 3, "city": 4, "combined": 5}
        available.sort(key=lambda option: (-option.gain, priority[option.kind], option.title))
        return available[:3]

    def recommend(self, req: RecommendationRequest) -> RecommendationResponse:
        # Stage 1: distinguish "there is no such category in this city" from filtering failures.
        city_category = self._city_category_candidates(req)

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
                plan_b=self._plan_b_city_for_absent(req),
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
                plan_b=self._build_plan_b(req, 0),
            )

        ranked = self._rank_valid(valid, req)

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
            plan_b=self._build_plan_b(req, len(valid)),
        )
