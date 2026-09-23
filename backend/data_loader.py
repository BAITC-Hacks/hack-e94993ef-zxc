from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class Contractor:
    id: str
    anon_name: str
    categories: tuple[str, ...]
    city: str
    city_imputed: bool
    synthetic: bool
    price_from_kzt: int
    price_imputed: bool
    event_formats: tuple[str, ...]
    languages: tuple[str, ...]
    max_hours: float | None
    busy_dates: frozenset[date]
    description: str


def _split_pipe(value: object) -> tuple[str, ...]:
    if value is None or pd.isna(value):
        return ()
    return tuple(part.strip() for part in str(value).split("|") if part.strip())


def _parse_busy_dates(value: object) -> frozenset[date]:
    dates: set[date] = set()
    for item in _split_pipe(value):
        dates.add(date.fromisoformat(item))
    return frozenset(dates)


def load_contractors(csv_path: str | Path) -> list[Contractor]:
    df = pd.read_csv(csv_path)
    contractors: list[Contractor] = []

    for row in df.to_dict(orient="records"):
        max_hours = None if pd.isna(row["max_hours"]) else float(row["max_hours"])
        contractors.append(
            Contractor(
                id=str(row["id"]),
                anon_name=str(row["anon_name"]),
                categories=_split_pipe(row["categories"]),
                city=str(row["city"]),
                city_imputed=bool(row["city_imputed"]),
                synthetic=bool(row["synthetic"]),
                price_from_kzt=int(row["price_from_kzt"]),
                price_imputed=bool(row["price_imputed"]),
                event_formats=_split_pipe(row["event_formats"]),
                languages=_split_pipe(row["languages"]),
                max_hours=max_hours,
                busy_dates=_parse_busy_dates(row["busy_dates"]),
                description=str(row.get("description") or ""),
            )
        )

    return contractors
