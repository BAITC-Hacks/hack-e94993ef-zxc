from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class RecommendationRequest(BaseModel):
    city: str = Field(min_length=1)
    event_date: date = Field(ge=date(2026, 9, 23), le=date(2026, 12, 31))
    event_format: str = Field(min_length=1)
    category: str = Field(min_length=1)
    budget_kzt: int = Field(gt=0)
    duration_hours: float | None = Field(default=None, gt=0)
    language: str | None = None
    preferences: str | None = Field(default=None, max_length=500)


class RecommendationCard(BaseModel):
    id: str
    name: str
    categories: list[str]
    city: str
    price_from_kzt: int
    event_formats: list[str]
    languages: list[str]
    max_hours: float | None
    score: float
    reasons: list[str]
    description: str
    synthetic: bool
    city_imputed: bool
    price_imputed: bool


class RecommendationResponse(BaseModel):
    status: Literal["matched", "category_absent", "no_match"]
    message: str
    results: list[RecommendationCard]
    diagnostics: dict[str, int | str | list[str]]
