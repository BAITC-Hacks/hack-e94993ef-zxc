from datetime import date
from pathlib import Path

from app.models import RecommendationRequest
from app.recommender import ContractorRecommender

DATA = Path(__file__).resolve().parent.parent / "data" / "contractors.csv"
engine = ContractorRecommender(DATA)


def request(**overrides):
    payload = dict(
        city="Алматы",
        event_date=date(2026, 10, 15),
        event_format="корпоратив",
        category="Ведущий",
        budget_kzt=1_500_000,
        duration_hours=6,
        language="русский",
        preferences="интеллигентный юмор импровизация современная подача",
    )
    payload.update(overrides)
    return RecommendationRequest(**payload)


def test_returns_at_most_three_and_explanations():
    result = engine.recommend(request())
    assert result.status == "matched"
    assert 1 <= len(result.results) <= 3
    assert all(1 <= len(card.reasons) <= 2 for card in result.results)


def test_deterministic_order():
    first = [x.id for x in engine.recommend(request()).results]
    second = [x.id for x in engine.recommend(request()).results]
    assert first == second


def test_busy_contractor_never_returned():
    result = engine.recommend(request())
    for card in result.results:
        source = next(c for c in engine.contractors if c.id == card.id)
        assert request().event_date not in source.busy_dates


def test_category_absent_is_distinct():
    result = engine.recommend(
        request(city="Астана", category="Флорист", event_format="свадьба", budget_kzt=1_000_000)
    )
    # The exact dataset decides whether Astana has the category; use an impossible city for a guaranteed branch.
    result = engine.recommend(request(city="Шымкент"))
    assert result.status == "category_absent"
    assert result.results == []


def test_no_match_is_explained():
    result = engine.recommend(request(budget_kzt=1))
    assert result.status == "no_match"
    assert not result.results
    assert "ни один" in result.message.lower()
