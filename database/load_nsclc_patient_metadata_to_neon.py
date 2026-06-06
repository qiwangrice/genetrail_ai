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
from load_nsclc_clinical_to_neon import (
    SURVIVAL_ATTRIBUTES,
    TREATMENT_ATTRIBUTE_KEYWORDS,
    _load_nsclc_studies,
)
from load_nsclc_to_neon import _load_patients_and_samples, _scan_dump_tables
from mysql_dump_parser import iter_insert_rows

PatientInt = int
PatientKey = Tuple[str, str]

# Preferred cBioPortal clinical_patient attribute IDs per normalized column.
# First matching attribute with a value wins for each column.
COLUMN_ATTR_PRIORITY: Dict[str, Tuple[str, ...]] = {
    "age": ("AGE", "AGE_AT_DIAGNOSIS", "AGE_AT_SEQ", "DIAGNOSIS_AGE", "PATIENT_AGE"),
    "sex": ("SEX", "GENDER"),
    "race": ("RACE", "PATIENT_RACE", "RACE_CATEGORY", "RACE_ANCHOR"),
    "ethnicity": ("ETHNICITY", "ETHNIC", "ETHNIC_GROUP"),
    "smoking_status": (
        "SMOKER",
        "SMOKING_STATUS",
        "SMOKING_HISTORY",
        "TOBACCO_HISTORY",
        "TOBACCO_USE",
        "CIGARETTES",
    ),
    "stage": (
        "AJCC_STAGE",
        "CLINICAL_STAGE",
        "STAGE",
        "TUMOR_STAGE",
        "OVERALL_STAGE",
    ),
    "ecog_status": ("ECOG", "ECOG_PS", "ECOG_AT_DIAGNOSIS", "ECOG_AT_SEQ"),
    "height_cm": ("HEIGHT", "HEIGHT_CM"),
    "weight_kg": ("WEIGHT", "WEIGHT_KG"),
    "bmi": ("BMI", "BODY_MASS_INDEX"),
    "country": ("COUNTRY", "COUNTRY_OF_ORIGIN"),
}

PRIORITY_ATTR_TO_COLUMN: Dict[str, str] = {
    attr_id: column
    for column, attr_ids in COLUMN_ATTR_PRIORITY.items()
    for attr_id in attr_ids
}

METADATA_KEYWORDS = (
    "AGE",
    "RACE",
    "SEX",
    "GENDER",
    "ETHNIC",
    "SMOK",
    "TOBACCO",
    "STAGE",
    "ECOG",
    "WEIGHT",
    "HEIGHT",
    "BMI",
    "COUNTRY",
    "REGION",
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS patient_metadata (
    study_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    age INTEGER,
    sex TEXT,
    race TEXT,
    ethnicity TEXT,
    smoking_status TEXT,
    stage TEXT,
    ecog_status TEXT,
    height_cm DOUBLE PRECISION,
    weight_kg DOUBLE PRECISION,
    bmi DOUBLE PRECISION,
    country TEXT,
    PRIMARY KEY (study_id, patient_id)
);

CREATE TABLE IF NOT EXISTS patient_metadata_attributes (
    study_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    attribute_id TEXT NOT NULL,
    attribute_value TEXT NOT NULL,
    PRIMARY KEY (study_id, patient_id, attribute_id)
);

CREATE INDEX IF NOT EXISTS idx_patient_metadata_study
    ON patient_metadata(study_id);
CREATE INDEX IF NOT EXISTS idx_patient_metadata_age
    ON patient_metadata(age);
CREATE INDEX IF NOT EXISTS idx_patient_metadata_race
    ON patient_metadata(race);
CREATE INDEX IF NOT EXISTS idx_patient_metadata_attributes_attr
    ON patient_metadata_attributes(attribute_id);
"""


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"NA", "N/A", "UNKNOWN", "UNK"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_age(value: str | None) -> int | None:
    parsed = _parse_float(value)
    if parsed is None:
        return None
    age = int(round(parsed))
    if age < 0 or age > 120:
        return None
    return age


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"NA", "N/A", "UNKNOWN", "UNK", "[NOT AVAILABLE]"}:
        return None
    return text


def _is_treatment_attribute(attr_id: str) -> bool:
    attr_upper = attr_id.upper()
    return any(keyword in attr_upper for keyword in TREATMENT_ATTRIBUTE_KEYWORDS)


def _is_metadata_attribute(attr_id: str) -> bool:
    attr_upper = attr_id.upper()
    if attr_upper in SURVIVAL_ATTRIBUTES:
        return False
    if _is_treatment_attribute(attr_upper):
        return False
    if attr_upper in PRIORITY_ATTR_TO_COLUMN:
        return True
    return any(keyword in attr_upper for keyword in METADATA_KEYWORDS)


def _empty_metadata_record(study_id: str, patient_id: str) -> dict:
    return {
        "study_id": study_id,
        "patient_id": patient_id,
        "age": None,
        "sex": None,
        "race": None,
        "ethnicity": None,
        "smoking_status": None,
        "stage": None,
        "ecog_status": None,
        "height_cm": None,
        "weight_kg": None,
        "bmi": None,
        "country": None,
        "raw_attrs": {},
    }


def _apply_attr_to_record(record: dict, attr_id: str, value_text: str) -> None:
    record["raw_attrs"][attr_id] = value_text
    column = PRIORITY_ATTR_TO_COLUMN.get(attr_id)
    if not column or record.get(column) is not None:
        return

    if column == "age":
        record["age"] = _parse_age(value_text)
    elif column in {"height_cm", "weight_kg", "bmi"}:
        record[column] = _parse_float(value_text)
    else:
        record[column] = _normalize_text(value_text)


def _load_metadata_rows(
    collected: Dict[str, List[str]],
    patient_rows: Dict[PatientInt, Tuple[str, str]],
    *,
    include_extra_attributes: bool,
) -> Tuple[Dict[PatientKey, dict], List[tuple]]:
    metadata_by_patient: Dict[PatientKey, dict] = {}
    extra_rows: List[tuple] = []
    extra_seen: Set[tuple] = set()

    for line in collected["clinical_patient"]:
        for row in iter_insert_rows(line, "clinical_patient"):
            if len(row) < 3:
                continue
            patient_int = row[0]
            attr_id = str(row[1] or "").upper()
            attr_value = row[2]
            if not isinstance(patient_int, int) or patient_int not in patient_rows:
                continue
            if attr_value is None or not _is_metadata_attribute(attr_id):
                continue

            study_id, stable_id = patient_rows[patient_int]
            value_text = _normalize_text(str(attr_value))
            if value_text is None:
                continue

            patient_key = (study_id, stable_id)
            record = metadata_by_patient.setdefault(
                patient_key,
                _empty_metadata_record(study_id, stable_id),
            )
            _apply_attr_to_record(record, attr_id, value_text)

            if not include_extra_attributes or attr_id in PRIORITY_ATTR_TO_COLUMN:
                continue

            dedupe_key = (study_id, stable_id, attr_id)
            if dedupe_key in extra_seen:
                continue
            extra_seen.add(dedupe_key)
            extra_rows.append((study_id, stable_id, attr_id, value_text))

    return metadata_by_patient, extra_rows


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def clear_metadata_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE patient_metadata_attributes, patient_metadata")
    conn.commit()


def load_metadata_to_neon(
    conn,
    metadata_by_patient: Dict[PatientKey, dict],
    extra_rows: List[tuple],
) -> None:
    metadata_values = [
        (
            record["study_id"],
            record["patient_id"],
            record["age"],
            record["sex"],
            record["race"],
            record["ethnicity"],
            record["smoking_status"],
            record["stage"],
            record["ecog_status"],
            record["height_cm"],
            record["weight_kg"],
            record["bmi"],
            record["country"],
        )
        for record in metadata_by_patient.values()
    ]

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO patient_metadata (
                study_id,
                patient_id,
                age,
                sex,
                race,
                ethnicity,
                smoking_status,
                stage,
                ecog_status,
                height_cm,
                weight_kg,
                bmi,
                country
            )
            VALUES %s
            ON CONFLICT (study_id, patient_id) DO UPDATE SET
                age = EXCLUDED.age,
                sex = EXCLUDED.sex,
                race = EXCLUDED.race,
                ethnicity = EXCLUDED.ethnicity,
                smoking_status = EXCLUDED.smoking_status,
                stage = EXCLUDED.stage,
                ecog_status = EXCLUDED.ecog_status,
                height_cm = EXCLUDED.height_cm,
                weight_kg = EXCLUDED.weight_kg,
                bmi = EXCLUDED.bmi,
                country = EXCLUDED.country
            """,
            metadata_values,
            page_size=5000,
        )
        if extra_rows:
            execute_values(
                cur,
                """
                INSERT INTO patient_metadata_attributes (
                    study_id, patient_id, attribute_id, attribute_value
                )
                VALUES %s
                ON CONFLICT (study_id, patient_id, attribute_id) DO UPDATE SET
                    attribute_value = EXCLUDED.attribute_value
                """,
                extra_rows,
                page_size=5000,
            )
    conn.commit()


def summarize(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM patient_metadata")
        metadata_count = cur.fetchone()[0]
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE age IS NOT NULL) AS with_age,
                COUNT(*) FILTER (WHERE sex IS NOT NULL) AS with_sex,
                COUNT(*) FILTER (WHERE race IS NOT NULL) AS with_race,
                COUNT(*) FILTER (WHERE ethnicity IS NOT NULL) AS with_ethnicity,
                COUNT(*) FILTER (WHERE smoking_status IS NOT NULL) AS with_smoking,
                COUNT(*) FILTER (WHERE stage IS NOT NULL) AS with_stage,
                COUNT(*) FILTER (WHERE ecog_status IS NOT NULL) AS with_ecog,
                ROUND(AVG(age)::numeric, 1) AS avg_age
            FROM patient_metadata
            """
        )
        (
            with_age,
            with_sex,
            with_race,
            with_ethnicity,
            with_smoking,
            with_stage,
            with_ecog,
            avg_age,
        ) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM patient_metadata_attributes")
        extra_count = cur.fetchone()[0]
        cur.execute(
            """
            SELECT race, COUNT(*) AS patient_count
            FROM patient_metadata
            WHERE race IS NOT NULL
            GROUP BY race
            ORDER BY patient_count DESC, race
            LIMIT 8
            """
        )
        top_races = cur.fetchall()

    print("\nLoaded NSCLC patient metadata:")
    print(f"  patient_metadata rows: {metadata_count}")
    print(f"    with age: {with_age} (avg age: {avg_age})")
    print(f"    with sex: {with_sex}")
    print(f"    with race: {with_race}")
    print(f"    with ethnicity: {with_ethnicity}")
    print(f"    with smoking status: {with_smoking}")
    print(f"    with stage: {with_stage}")
    print(f"    with ECOG: {with_ecog}")
    print(f"  extra metadata attributes: {extra_count}")
    if top_races:
        print("  top race values:")
        for race, patient_count in top_races:
            print(f"    {race}: {patient_count}")


def main(argv: List[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Load NSCLC patient demographic/clinical metadata (age, race, sex, etc.) "
            "from a cBioPortal MySQL dump into Neon Postgres."
        )
    )
    parser.add_argument(
        "--dump",
        default="/Users/qiwang/Downloads/dump_2026_04_18_v2_14_5.sql.gz",
        help="Path to cBioPortal MySQL dump (.sql or .sql.gz)",
    )
    parser.add_argument(
        "--include-extra-attributes",
        action="store_true",
        help=(
            "Also store other demographic-like clinical_patient attributes in "
            "patient_metadata_attributes."
        ),
    )
    parser.add_argument(
        "--skip-clear",
        action="store_true",
        help="Do not truncate patient_metadata tables before loading",
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.dump):
        print(f"Dump file not found: {args.dump}", file=sys.stderr)
        return 1

    print(f"Loading NSCLC patient metadata from: {args.dump}", flush=True)

    tables = {
        "type_of_cancer",
        "cancer_study",
        "patient",
        "sample",
        "clinical_patient",
    }
    print("Scanning dump for NSCLC metadata tables...", flush=True)
    collected = _scan_dump_tables(args.dump, tables)

    studies = _load_nsclc_studies(collected)
    if not studies:
        print("No NSCLC studies found in dump.", file=sys.stderr)
        return 1

    patient_rows, _sample_to_patient = _load_patients_and_samples(collected, studies)
    metadata_by_patient, extra_rows = _load_metadata_rows(
        collected,
        patient_rows,
        include_extra_attributes=args.include_extra_attributes,
    )

    print(
        f"  metadata rows kept: {len(metadata_by_patient)} | "
        f"extra attribute rows: {len(extra_rows)}",
        flush=True,
    )

    conn = psycopg2.connect(get_database_url())
    try:
        init_schema(conn)
        if not args.skip_clear:
            clear_metadata_tables(conn)
        load_metadata_to_neon(conn, metadata_by_patient, extra_rows)
        summarize(conn)
    finally:
        conn.close()

    print(
        "\nDone. Neon Postgres now contains NSCLC patient_metadata "
        "(age, race, sex, ethnicity, smoking, stage, ECOG, etc.)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
