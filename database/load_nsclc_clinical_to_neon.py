from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from cbioportal_search import get_database_url
from load_nsclc_to_neon import (
    _is_nsclc_study_row,
    _is_nsclc_type_row,
    _load_patients_and_samples,
    _scan_dump_tables,
)
from mysql_dump_parser import iter_insert_rows

SurvivalKey = Tuple[str, str]
PatientInt = int

SURVIVAL_ATTRIBUTES = {
    "OS_STATUS",
    "OS_MONTHS",
    "DFS_STATUS",
    "DFS_MONTHS",
    "VITAL_STATUS",
}

TREATMENT_ATTRIBUTE_KEYWORDS = (
    "TREATMENT",
    "_TX",
    "CHEMO",
    "THERAPY",
    "REGIMEN",
    "RADIATION",
    "IMMUNOTHERAPY",
    "TARGETED",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS patient_survival (
    study_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    os_status TEXT,
    os_months DOUBLE PRECISION,
    os_days DOUBLE PRECISION,
    dfs_status TEXT,
    dfs_months DOUBLE PRECISION,
    dfs_days DOUBLE PRECISION,
    vital_status TEXT,
    is_deceased BOOLEAN,
    PRIMARY KEY (study_id, patient_id)
);

CREATE TABLE IF NOT EXISTS patient_treatments (
    study_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    attribute_id TEXT NOT NULL,
    attribute_label TEXT,
    treatment_value TEXT NOT NULL,
    PRIMARY KEY (study_id, patient_id, attribute_id, treatment_value)
);

CREATE INDEX IF NOT EXISTS idx_patient_survival_study
    ON patient_survival(study_id);
CREATE INDEX IF NOT EXISTS idx_patient_treatments_study
    ON patient_treatments(study_id);
CREATE INDEX IF NOT EXISTS idx_patient_treatments_attr
    ON patient_treatments(attribute_id);
"""

ATTRIBUTE_LABELS = {
    "OS_STATUS": "Overall survival status",
    "OS_MONTHS": "Overall survival months",
    "DFS_STATUS": "Disease-free survival status",
    "DFS_MONTHS": "Disease-free survival months",
    "VITAL_STATUS": "Vital status",
    "PRIOR_TREATMENT": "Prior treatment",
    "PRIOR_TX": "Prior treatment",
    "SYSTEMIC_TREATMENT": "Systemic treatment",
    "FIRST_LINE_TREATMENT": "First-line treatment",
    "2L_TREATMENT": "Second-line treatment",
    "3L_TREATMENT": "Third-line treatment",
    "NEOADJUVANT_CHEMO": "Neoadjuvant chemotherapy",
    "ADJUVANT_TX": "Adjuvant treatment",
    "ADJUVANT_CHEMO": "Adjuvant chemotherapy",
    "RADIATION_THERAPY": "Radiation therapy",
}


def _load_nsclc_studies(collected: Dict[str, List[str]]) -> Dict[int, dict]:
    nsclc_type_ids: Set[str] = set()
    for line in collected["type_of_cancer"]:
        for row in iter_insert_rows(line, "type_of_cancer"):
            if _is_nsclc_type_row(row):
                nsclc_type_ids.add(str(row[0]))

    studies: Dict[int, dict] = {}
    for line in collected["cancer_study"]:
        for row in iter_insert_rows(line, "cancer_study"):
            if len(row) < 4:
                continue
            study_int = int(row[0])
            cancer_type_id = str(row[2] or "")
            if _is_nsclc_study_row(row) or cancer_type_id in nsclc_type_ids:
                studies[study_int] = {
                    "study_id": str(row[1]),
                    "study_name": str(row[3]),
                    "cancer_type_id": cancer_type_id,
                }
    print(f"  NSCLC studies: {len(studies)}", flush=True)
    return studies


def _is_treatment_attribute(attr_id: str, allowed_attrs: Set[str] | None) -> bool:
    attr_upper = attr_id.upper()
    if allowed_attrs and attr_upper in allowed_attrs:
        return True
    if allowed_attrs is not None:
        return False
    return any(keyword in attr_upper for keyword in TREATMENT_ATTRIBUTE_KEYWORDS)


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "NA":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _months_to_days(months: float | None) -> float | None:
    if months is None:
        return None
    return round(months * 30.437, 2)


def _normalize_deceased(os_status: str | None, vital_status: str | None) -> bool | None:
    if os_status:
        status = os_status.upper()
        if status.startswith("1:") or "DECEASED" in status or "DEAD" in status:
            return True
        if status.startswith("0:") or "LIVING" in status or "ALIVE" in status:
            return False
    if vital_status:
        status = vital_status.upper()
        if status in {"DEAD", "DECEASED", "DIED"}:
            return True
        if status in {"ALIVE", "LIVING"}:
            return False
    return None


def _normalize_survival_status(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.upper() == "NA":
        return None
    upper = text.upper()
    if upper.startswith("0:"):
        return "Living"
    if upper.startswith("1:"):
        return "Deceased"
    if upper in {"ALIVE", "LIVING"}:
        return "Living"
    if upper in {"DEAD", "DECEASED", "DIED"}:
        return "Deceased"
    return text


def _load_clinical_rows(
    collected: Dict[str, List[str]],
    patient_rows: Dict[PatientInt, Tuple[str, str]],
    treatment_attrs: Set[str] | None,
) -> Tuple[Dict[SurvivalKey, dict], List[tuple]]:
    survival_by_patient: Dict[SurvivalKey, dict] = {}
    treatment_rows: List[tuple] = []
    treatment_seen: Set[tuple] = set()

    for line in collected["clinical_patient"]:
        for row in iter_insert_rows(line, "clinical_patient"):
            if len(row) < 3:
                continue
            patient_int = row[0]
            attr_id = str(row[1] or "").upper()
            attr_value = row[2]
            if not isinstance(patient_int, int) or patient_int not in patient_rows:
                continue
            if attr_value is None:
                continue

            study_id, stable_id = patient_rows[patient_int]
            value_text = str(attr_value).strip()
            if not value_text or value_text.upper() == "NA":
                continue
            patient_key = (study_id, stable_id)

            if attr_id in SURVIVAL_ATTRIBUTES:
                record = survival_by_patient.setdefault(
                    patient_key,
                    {
                        "study_id": study_id,
                        "patient_id": stable_id,
                        "os_status": None,
                        "os_months": None,
                        "os_days": None,
                        "dfs_status": None,
                        "dfs_months": None,
                        "dfs_days": None,
                        "vital_status": None,
                        "is_deceased": None,
                    },
                )
                if attr_id == "OS_STATUS":
                    record["os_status"] = _normalize_survival_status(value_text)
                elif attr_id == "OS_MONTHS":
                    record["os_months"] = _parse_float(value_text)
                    record["os_days"] = _months_to_days(record["os_months"])
                elif attr_id == "DFS_STATUS":
                    record["dfs_status"] = _normalize_survival_status(value_text)
                elif attr_id == "DFS_MONTHS":
                    record["dfs_months"] = _parse_float(value_text)
                    record["dfs_days"] = _months_to_days(record["dfs_months"])
                elif attr_id == "VITAL_STATUS":
                    record["vital_status"] = _normalize_survival_status(value_text)
                continue

            if not _is_treatment_attribute(attr_id, treatment_attrs):
                continue

            dedupe_key = (study_id, stable_id, attr_id, value_text)
            if dedupe_key in treatment_seen:
                continue
            treatment_seen.add(dedupe_key)
            treatment_rows.append(
                (
                    study_id,
                    stable_id,
                    attr_id,
                    ATTRIBUTE_LABELS.get(attr_id, attr_id.replace("_", " ").title()),
                    value_text,
                )
            )

    for record in survival_by_patient.values():
        record["is_deceased"] = _normalize_deceased(
            record["os_status"],
            record["vital_status"],
        )

    return survival_by_patient, treatment_rows


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def clear_clinical_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE patient_treatments, patient_survival")
    conn.commit()


def load_clinical_to_neon(
    conn,
    survival_by_patient: Dict[SurvivalKey, dict],
    treatment_rows: List[tuple],
) -> None:
    survival_values = [
        (
            record["study_id"],
            record["patient_id"],
            record["os_status"],
            record["os_months"],
            record["os_days"],
            record["dfs_status"],
            record["dfs_months"],
            record["dfs_days"],
            record["vital_status"],
            record["is_deceased"],
        )
        for record in survival_by_patient.values()
    ]

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO patient_survival (
                study_id,
                patient_id,
                os_status,
                os_months,
                os_days,
                dfs_status,
                dfs_months,
                dfs_days,
                vital_status,
                is_deceased
            )
            VALUES %s
            ON CONFLICT (study_id, patient_id) DO UPDATE SET
                os_status = EXCLUDED.os_status,
                os_months = EXCLUDED.os_months,
                os_days = EXCLUDED.os_days,
                dfs_status = EXCLUDED.dfs_status,
                dfs_months = EXCLUDED.dfs_months,
                dfs_days = EXCLUDED.dfs_days,
                vital_status = EXCLUDED.vital_status,
                is_deceased = EXCLUDED.is_deceased
            """,
            survival_values,
            page_size=5000,
        )
        execute_values(
            cur,
            """
            INSERT INTO patient_treatments (
                study_id, patient_id, attribute_id, attribute_label, treatment_value
            )
            VALUES %s
            ON CONFLICT (study_id, patient_id, attribute_id, treatment_value) DO NOTHING
            """,
            treatment_rows,
            page_size=5000,
        )
    conn.commit()


def summarize(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM patient_survival")
        survival_count = cur.fetchone()[0]
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE is_deceased IS TRUE) AS deceased,
                COUNT(*) FILTER (WHERE is_deceased IS FALSE) AS living,
                COUNT(*) FILTER (WHERE os_months IS NOT NULL) AS with_os_months,
                ROUND(AVG(os_months)::numeric, 2) AS avg_os_months
            FROM patient_survival
            """
        )
        deceased, living, with_os_months, avg_os_months = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM patient_treatments")
        treatment_count = cur.fetchone()[0]
        cur.execute(
            """
            SELECT attribute_id, COUNT(*) AS row_count
            FROM patient_treatments
            GROUP BY attribute_id
            ORDER BY row_count DESC, attribute_id
            LIMIT 10
            """
        )
        top_treatment_attrs = cur.fetchall()

    print("\nLoaded NSCLC clinical data:")
    print(f"  survival rows: {survival_count}")
    print(f"    deceased: {deceased}")
    print(f"    living: {living}")
    print(f"    with OS months: {with_os_months}")
    print(f"    average OS months: {avg_os_months}")
    print(f"  treatment rows: {treatment_count}")
    if top_treatment_attrs:
        print("  top treatment attributes:")
        for attr_id, row_count in top_treatment_attrs:
            print(f"    {attr_id}: {row_count}")


def _parse_treatment_attrs(raw: str | None) -> Set[str] | None:
    if not raw:
        return None
    attrs = {item.strip().upper() for item in raw.split(",") if item.strip()}
    return attrs or None


def main(argv: List[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Load NSCLC patient treatment and survival/status data from a cBioPortal "
            "MySQL dump into Neon Postgres."
        )
    )
    parser.add_argument(
        "--dump",
        default="/Users/qiwang/Downloads/dump_2026_04_18_v2_14_5.sql.gz",
        help="Path to cBioPortal MySQL dump (.sql or .sql.gz)",
    )
    parser.add_argument(
        "--treatment-attrs",
        default="",
        help=(
            "Optional comma-separated clinical attribute IDs to load as treatment. "
            "If omitted, loads attributes matching treatment/chemo/therapy keywords."
        ),
    )
    parser.add_argument(
        "--skip-clear",
        action="store_true",
        help="Do not truncate patient_survival and patient_treatments before loading",
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.dump):
        print(f"Dump file not found: {args.dump}", file=sys.stderr)
        return 1

    treatment_attrs = _parse_treatment_attrs(args.treatment_attrs)
    print(f"Loading NSCLC clinical data from: {args.dump}", flush=True)

    tables = {
        "type_of_cancer",
        "cancer_study",
        "patient",
        "sample",
        "clinical_patient",
    }
    print("Scanning dump for NSCLC clinical tables...", flush=True)
    collected = _scan_dump_tables(args.dump, tables)

    studies = _load_nsclc_studies(collected)
    if not studies:
        print("No NSCLC studies found in dump.", file=sys.stderr)
        return 1

    patient_rows, _sample_to_patient = _load_patients_and_samples(collected, studies)
    survival_by_patient, treatment_rows = _load_clinical_rows(
        collected,
        patient_rows,
        treatment_attrs,
    )

    print(
        f"  survival rows kept: {len(survival_by_patient)} | "
        f"treatment rows kept: {len(treatment_rows)}",
        flush=True,
    )

    conn = psycopg2.connect(get_database_url())
    try:
        init_schema(conn)
        if not args.skip_clear:
            clear_clinical_tables(conn)
        load_clinical_to_neon(conn, survival_by_patient, treatment_rows)
        summarize(conn)
    finally:
        conn.close()

    print(
        "\nDone. Neon Postgres now contains NSCLC patient survival/status and treatment rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
