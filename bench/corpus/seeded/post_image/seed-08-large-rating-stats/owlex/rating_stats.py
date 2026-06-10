"""Aggregation and quantile helpers for blind council ratings."""
from __future__ import annotations

import math


MIN_RATING = 1
MAX_RATING = 10


def clamp_rating(value: float) -> float:
    if value < MIN_RATING:
        return float(MIN_RATING)
    if value > MAX_RATING:
        return float(MAX_RATING)
    return float(value)


def normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return [0.5 for _ in values]
    span = hi - lo
    return [(v - lo) / span for v in values]


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def stdev(values: list[float]) -> float:
    return math.sqrt(variance(values))


def trimmed_mean(values: list[float], trim: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = int(len(ordered) * trim)
    if k > 0:
        ordered = ordered[k:-k]
    if not ordered:
        return 0.0
    return mean(ordered)


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(len(ordered) * q)
    return ordered[idx]


def median(values: list[float]) -> float:
    return quantile(values, 0.5)


def spread(values: list[float]) -> float:
    if not values:
        return 0.0
    return quantile(values, 0.9) - quantile(values, 0.1)


def aggregate_seat(ratings: list[float]) -> dict[str, float]:
    return {
        "mean": round(mean(ratings), 4),
        "stdev": round(stdev(ratings), 4),
        "median": round(median(ratings), 4),
        "p90": round(quantile(ratings, 0.9), 4),
        "p100": round(quantile(ratings, 1.0), 4),
    }


def rank_seats(seat_ratings: dict[str, list[float]]) -> list[str]:
    scored = [(seat, mean(r)) for seat, r in seat_ratings.items()]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [seat for seat, _ in scored]


def summarize(seat_ratings: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    return {seat: aggregate_seat(r) for seat, r in seat_ratings.items()}
