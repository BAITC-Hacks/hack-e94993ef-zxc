from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health_and_meta():
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    meta = client.get("/api/meta")
    assert meta.status_code == 200
    assert meta.json()["profiles_count"] == 66


def test_recommend_endpoint():
    response = client.post(
        "/api/recommend",
        json={
            "city": "Алматы",
            "event_date": "2026-10-15",
            "event_format": "корпоратив",
            "category": "Ведущий",
            "budget_kzt": 1500000,
            "duration_hours": 6,
            "language": "русский",
            "preferences": "интеллигентный юмор импровизация современная подача",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "matched"
    assert 1 <= len(payload["results"]) <= 3


def test_plan_b_is_returned_for_strict_request():
    response = client.post(
        "/api/recommend",
        json={
            "city": "Астана",
            "event_date": "2026-12-31",
            "event_format": "свадьба",
            "category": "Ведущий",
            "budget_kzt": 300000,
            "duration_hours": 10,
            "language": "казахский",
            "preferences": None,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "no_match"
    assert payload["plan_b"]
    assert payload["plan_b"][0]["candidate_count"] >= 1
    assert payload["plan_b"][0]["patch"]
