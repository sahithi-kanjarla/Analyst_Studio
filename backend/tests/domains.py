"""Synthetic datasets from unrelated domains. The question logic must behave sensibly on all of them without knowing any domain."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _dates(rng, n, start="2024-01-01", span=300):
    return pd.Timestamp(start) + pd.to_timedelta(rng.integers(0, span, n), unit="D")


def hr_attrition(n=500, seed=1):
    """Two dates, a -1 placeholder in a rating column, a column that is the sum of two others."""
    rng = np.random.default_rng(seed)
    hire = _dates(rng, n, "2018-01-01", 1500)
    base, bonus = rng.normal(60000, 9000, n).round(0), rng.integers(0, 8000, n).astype(float)
    rating = rng.integers(1, 6, n).astype(float) + rng.normal(0, .05, n).round(2)
    rating[rng.random(n) < 0.14] = -1
    return pd.DataFrame({"hired_on": hire.strftime("%Y-%m-%d"), "last_review": (hire + pd.to_timedelta(rng.integers(100, 900, n), unit="D")).strftime("%Y-%m-%d"),
                         "team": rng.choice(["A", "B", "C", "D"], n), "left_company": rng.choice([0, 1], n, p=[.8, .2]),
                         "base_pay": base, "bonus": bonus, "total_pay": base + bonus, "perf_rating": rating})


def hospital_stays(n=500, seed=2):
    """Admit/discharge dates, a 9999 placeholder for an unknown length, negative adjustments."""
    rng = np.random.default_rng(seed)
    adm = _dates(rng, n, "2024-01-01", 330)
    los = rng.integers(1, 15, n).astype(float) + rng.normal(0, .1, n).round(2)
    los[rng.random(n) < 0.11] = 9999
    cost = rng.gamma(3, 1500, n).round(2)
    cost[rng.random(n) < 0.04] *= -1
    return pd.DataFrame({"admitted": adm.strftime("%Y-%m-%d"), "discharged": (adm + pd.to_timedelta(rng.integers(1, 15, n), unit="D")).strftime("%Y-%m-%d"),
                         "ward": rng.choice(["ICU", "General", "Maternity"], n), "stay_days": los, "cost": cost, "readmitted": rng.choice([0, 1], n, p=[.85, .15])})


def shipment_delays(n=600, seed=3):
    """Planned vs actual dates, early arrivals (negative delay), 'Unknown' carrier labels, two identical columns."""
    rng = np.random.default_rng(seed)
    planned = _dates(rng, n)
    delay = rng.normal(40, 60, n).round(0)
    delay[delay < -20] = 5
    delay[rng.random(n) < 0.06] = -rng.integers(5, 60, int((rng.random(n) < 0).sum()) or 1).max()    # a few early arrivals
    carrier = rng.choice(["FastCo", "Haul", "Unknown", "RoadX"], n, p=[.35, .3, .15, .2])
    dist = rng.gamma(4, 120, n).round(1)
    return pd.DataFrame({"planned_on": planned.strftime("%Y-%m-%d"), "arrived_on": (planned + pd.to_timedelta(np.maximum(delay, 0) // 60 + 2, unit="D")).strftime("%Y-%m-%d"),
                         "carrier": carrier, "delay_minutes": delay, "distance_km": dist, "distance_copy": dist})


def exam_scores(n=400, seed=4):
    """One date, a total that is the sum of two parts, a 999 'absent' code, very rare labels."""
    rng = np.random.default_rng(seed)
    a, b = rng.normal(55, 15, n).round(0), rng.normal(50, 12, n).round(0)
    attend = rng.integers(40, 100, n).astype(float)
    attend[rng.random(n) < 0.10] = 999
    school = rng.choice(["North", "South", "East", "West"], n).astype(object)
    school[:2], school[2:4] = "Zq1", "Zq2"
    return pd.DataFrame({"exam_day": _dates(rng, n, "2024-05-01", 20).strftime("%Y-%m-%d"), "school": school, "part_one": a, "part_two": b, "overall": a + b, "attendance": attend})


def sensor_readings(n=800, seed=5):
    """Signed readings (half negative: must NOT trigger a negatives question), -9999 fault code, a mostly empty column."""
    rng = np.random.default_rng(seed)
    temp = rng.normal(0, 8, n).round(2)
    temp[rng.random(n) < 0.08] = -9999
    mostly_empty = rng.normal(5, 1, n)
    mostly_empty[rng.random(n) < 0.7] = np.nan
    return pd.DataFrame({"taken_at": pd.date_range("2025-01-01", periods=n, freq="h").strftime("%Y-%m-%d %H:%M:%S"), "unit": rng.choice(["u1", "u2", "u3"], n),
                         "temp": temp, "pressure": rng.normal(1000, 25, n).round(1), "spare_channel": mostly_empty})


def survey(n=500, seed=6):
    """Likert answers with a 99 'don't know' code, 'n/a' text answers, one date."""
    rng = np.random.default_rng(seed)
    q1 = rng.integers(1, 6, n).astype(float)
    q1[rng.random(n) < 0.12] = 99
    channel = rng.choice(["web", "phone", "n/a", "in person"], n, p=[.45, .25, .12, .18])
    return pd.DataFrame({"answered_on": _dates(rng, n, "2025-02-01", 60).strftime("%Y-%m-%d"), "q1_satisfaction": q1, "q2_effort": rng.integers(1, 8, n), "channel": channel,
                         "respondent_group": rng.choice(["x", "y", "z"], n)})


def clean_table(n=300, seed=7):
    """Nothing ambiguous."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"day": pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d"), "group": rng.choice(list("NSEW"), n), "visits": rng.normal(100, 10, n).round(1)})


DOMAINS = {"hr": hr_attrition, "hospital": hospital_stays, "shipments": shipment_delays, "exams": exam_scores, "sensors": sensor_readings, "survey": survey}
