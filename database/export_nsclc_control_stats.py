from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cbioportal_search import (
    PatientKey,
    _summarize_os_days,
    _summarize_os_status,
    _treatment_value_is_informative,
    get_database_url,
)

DEFAULT_OUTPUT = ROOT / "resources" / "control.csv"


def _ensure_tables(cur) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS table_count
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN (
              'patients',
              'patient_survival',
              'patient_treatments'
          )
        """
    )
    if cur.fetchone()["table_count"] < 3:
        raise RuntimeError(
            "Neon Postgres is missing required tables. Run:\n"
            "  poetry run python database/load_nsclc_to_neon.py\n"
            "  poetry run python database/load_nsclc_clinical_to_neon.py"
        )


def _fetch_all_patient_keys(cur) -> List[PatientKey]:
    cur.execute(
        """
        SELECT study_id, patient_id
        FROM patients
        ORDER BY study_id, patient_id
        """
    )
    return [(row["study_id"], row["patient_id"]) for row in cur.fetchall()]


def _fetch_survival_by_patient(cur) -> Dict[PatientKey, dict]:
    cur.execute(
        """
        SELECT study_id, patient_id, os_status, os_days
        FROM patient_survival
        """
    )
    return {(row["study_id"], row["patient_id"]): row for row in cur.fetchall()}


def _fetch_patients_with_any_treatment(cur) -> Set[PatientKey]:
    cur.execute(
        """
        SELECT study_id, patient_id, treatment_value
        FROM patient_treatments
        """
    )
    patients_with_treatment: Set[PatientKey] = set()
    for row in cur.fetchall():
        if _treatment_value_is_informative(str(row["treatment_value"] or "")):
            patients_with_treatment.add((row["study_id"], row["patient_id"]))
    return patients_with_treatment


def _build_patient_records(
    patient_keys: Iterable[PatientKey],
    survival_by_patient: Dict[PatientKey, dict],
    patients_with_treatment: Set[PatientKey],
) -> List[dict]:
    records: List[dict] = []
    for key in patient_keys:
        survival = survival_by_patient.get(key, {})
        records.append(
            {
                "study_id": key[0],
                "patient_id": key[1],
                "os_status": survival.get("os_status"),
                "os_days": survival.get("os_days"),
                "has_treatment": key in patients_with_treatment,
            }
        )
    return records


def _rows_to_csv(records: List[dict], bin_count: int) -> List[dict]:
    rows: List[dict] = []

    rows.append(
        {
            "metric": "patient_count",
            "group": "all",
            "category": "total_nsclc_patients",
            "count": len(records),
        }
    )

    with_treatment = [record for record in records if record["has_treatment"]]
    without_treatment = [record for record in records if not record["has_treatment"]]

    rows.append(
        {
            "metric": "patient_count",
            "group": "with_treatment",
            "category": "patients_with_any_treatment",
            "count": len(with_treatment),
        }
    )
    rows.append(
        {
            "metric": "patient_count",
            "group": "without_treatment",
            "category": "patients_without_treatment",
            "count": len(without_treatment),
        }
    )

    for item in _summarize_os_status(records):
        rows.append(
            {
                "metric": "survival_status",
                "group": "all",
                "category": item["status"],
                "count": item["count"],
            }
        )

    for group_name, group_records in (
        ("with_treatment", with_treatment),
        ("without_treatment", without_treatment),
    ):
        for item in _summarize_os_status(group_records):
            rows.append(
                {
                    "metric": "survival_status",
                    "group": group_name,
                    "category": item["status"],
                    "count": item["count"],
                }
            )

    for group_name, group_records in (
        ("with_treatment", with_treatment),
        ("without_treatment", without_treatment),
    ):
        os_days_records = [
            record
            for record in group_records
            if record.get("os_days") is not None and record["os_days"] >= 0
        ]
        for item in _summarize_os_days(os_days_records, bin_count=bin_count):
            rows.append(
                {
                    "metric": "survival_days",
                    "group": group_name,
                    "category": item["label"],
                    "count": item["count"],
                }
            )

    rows.append(
        {
            "metric": "survival_days",
            "group": "with_treatment",
            "category": "patients_with_os_days",
            "count": sum(1 for record in with_treatment if record["os_days"] is not None),
        }
    )
    rows.append(
        {
            "metric": "survival_days",
            "group": "without_treatment",
            "category": "patients_with_os_days",
            "count": sum(
                1 for record in without_treatment if record["os_days"] is not None
            ),
        }
    )

    return rows


def write_control_csv(rows: Iterable[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["metric", "group", "category", "count"]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: List[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Export aggregate NSCLC survival control statistics to resources/control.csv"
        )
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to output CSV file",
    )
    parser.add_argument(
        "--bins",
        type=int,
        default=6,
        help="Number of bins for survival days distribution",
    )
    args = parser.parse_args(argv)

    conn = psycopg2.connect(get_database_url())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _ensure_tables(cur)
            patient_keys = _fetch_all_patient_keys(cur)
            survival_by_patient = _fetch_survival_by_patient(cur)
            patients_with_treatment = _fetch_patients_with_any_treatment(cur)
    finally:
        conn.close()

    records = _build_patient_records(
        patient_keys,
        survival_by_patient,
        patients_with_treatment,
    )
    rows = _rows_to_csv(records, bin_count=args.bins)
    output_path = Path(args.output)
    write_control_csv(rows, output_path)

    print(f"Wrote {len(rows)} aggregate rows to {output_path}")
    print(f"  total NSCLC patients: {len(records)}")
    print(f"  with any treatment: {sum(1 for record in records if record['has_treatment'])}")
    print(
        "  without treatment: "
        f"{sum(1 for record in records if not record['has_treatment'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
