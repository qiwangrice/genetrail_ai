from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, Iterable

DEFAULT_CONTROL_PATH = Path(__file__).resolve().parent / "resources" / "control.csv"
DAY_BIN_PATTERN = re.compile(r"([\d.]+)\s*[–-]\s*([\d.]+)\s*days", re.IGNORECASE)
SINGLE_DAY_PATTERN = re.compile(r"([\d.]+)\s*days", re.IGNORECASE)


def _parse_count(value: str) -> int:
    return int(float(value))


def _parse_day_bin_midpoint(label: str) -> float | None:
    range_match = DAY_BIN_PATTERN.search(label)
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        return (low + high) / 2

    single_match = SINGLE_DAY_PATTERN.search(label)
    if single_match:
        return float(single_match.group(1))
    return None


def _average_survival_days(rows: Iterable[dict]) -> float | None:
    total_weight = 0
    weighted_sum = 0.0
    for row in rows:
        if row["category"] == "patients_with_os_days":
            continue
        midpoint = _parse_day_bin_midpoint(row["category"])
        if midpoint is None:
            continue
        count = row["count"]
        weighted_sum += midpoint * count
        total_weight += count
    if total_weight == 0:
        return None
    return round(weighted_sum / total_weight, 1)


def _living_percentage(status_rows: Iterable[dict], group_total: int) -> float | None:
    if group_total <= 0:
        return None
    living = next(
        (row["count"] for row in status_rows if row["category"] == "Living"),
        0,
    )
    return round(100 * living / group_total, 1)


STATUS_ORDER = ("Living", "Deceased", "Unknown")


def _status_distribution(status_rows: Iterable[dict], group_total: int) -> list[dict]:
    if group_total <= 0:
        return []

    by_category = {row["category"]: row["count"] for row in status_rows}
    return [
        {
            "status": status,
            "count": by_category.get(status, 0),
            "percentage": round(100 * by_category.get(status, 0) / group_total, 1),
        }
        for status in STATUS_ORDER
    ]


def load_control_stats(path: Path | None = None) -> dict:
    csv_path = path or DEFAULT_CONTROL_PATH
    if not csv_path.exists():
        raise RuntimeError(
            f"Control file not found: {csv_path}. Run:\n"
            "  poetry run python database/export_nsclc_control_stats.py"
        )

    rows: list[dict] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                {
                    "metric": row["metric"],
                    "group": row["group"],
                    "category": row["category"],
                    "count": _parse_count(row["count"]),
                }
            )

    grouped: Dict[str, Dict[str, list[dict]]] = {}
    for row in rows:
        grouped.setdefault(row["group"], {}).setdefault(row["metric"], []).append(row)

    summaries: Dict[str, dict] = {}
    for group in ("with_treatment", "without_treatment"):
        group_rows = grouped.get(group, {})
        patient_rows = group_rows.get("patient_count", [])
        group_total = next(
            (row["count"] for row in patient_rows if row["category"].startswith("patients_")),
            0,
        )
        status_rows = group_rows.get("survival_status", [])
        day_rows = group_rows.get("survival_days", [])

        summaries[group] = {
            "patient_count": group_total,
            "living_percentage": _living_percentage(status_rows, group_total),
            "average_survival_days": _average_survival_days(day_rows),
            "os_status_distribution": _status_distribution(status_rows, group_total),
            "patients_with_os_days": next(
                (
                    row["count"]
                    for row in day_rows
                    if row["category"] == "patients_with_os_days"
                ),
                0,
            ),
        }

    return {
        "source": str(csv_path.name),
        "with_treatment": summaries["with_treatment"],
        "without_treatment": summaries["without_treatment"],
    }
