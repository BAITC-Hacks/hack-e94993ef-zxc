from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import RecommendationRequest, RecommendationResponse
from .recommender import ContractorRecommender

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "contractors.csv"
STATIC_DIR = BASE_DIR / "frontend"

app = FastAPI(
    title="Smart Contractor",
    version="1.0.0",
    description="Explainable contractor recommendation service",
)
recommender = ContractorRecommender(DATA_PATH)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def metadata() -> dict:
    return recommender.metadata()


@app.post("/api/recommend", response_model=RecommendationResponse)
def recommend(payload: RecommendationRequest) -> RecommendationResponse:
    return recommender.recommend(payload)
