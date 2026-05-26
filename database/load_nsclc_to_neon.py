from __future__ import annotations

import argparse
import gzip
import os
import re
import sys
import time
from typing import Dict, Iterable, List, Set, Tuple

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

from cbioportal_search import DEFAULT_NSCLC_GENES, get_database_url
from mysql_dump_parser import iter_insert_rows

NSCLC_TYPE_KEYWORDS = {
    "nsclc",
    "luad",
    "lusc",
    "lung",
    "pluad",
    "pluc",
    "nosclc",
    "nsccl",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS studies (
    study_id TEXT PRIMARY KEY,
    study_name TEXT NOT NULL,
    cancer_type_id TEXT
);

CREATE TABLE IF NOT EXISTS patients (
    study_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    stable_id TEXT,
    PRIMARY KEY (study_id, patient_id)
);

CREATE TABLE IF NOT EXISTS patient_mutations (
    study_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    gene_symbol TEXT NOT NULL,
    protein_change TEXT,
    entrez_gene_id INTEGER,
    PRIMARY KEY (study_id, patient_id, gene_symbol, protein_change)
);

CREATE INDEX IF NOT EXISTS idx_patients_study ON patients(study_id);
CREATE INDEX IF NOT EXISTS idx_mutations_gene ON patient_mutations(gene_symbol);
CREATE INDEX IF NOT EXISTS idx_mutations_study_gene
    ON patient_mutations(study_id, gene_symbol);
"""


def _is_nsclc_study_row(row: List) -> bool:
    if len(row) < 4:
        return False
    identifier = str(row[1] or "").lower()
    cancer_type_id = str(row[2] or "").lower()
    name = str(row[3] or "").lower()
    haystack = " ".join([identifier, cancer_type_id, name])
    if "small cell" in haystack and "non-small cell" not in haystack:
        return False
    return any(keyword in haystack for keyword in NSCLC_TYPE_KEYWORDS)


def _is_nsclc_type_row(row: List) -> bool:
    if not row:
        return False
    type_id = str(row[0] or "").lower()
    name = str(row[1] or "").lower() if len(row) > 1 else ""
    parent = str(row[-1] or "").lower()
    haystack = " ".join([type_id, name, parent])
    if "small cell" in haystack and "non-small cell" not in haystack:
        return False
    return any(keyword in haystack for keyword in NSCLC_TYPE_KEYWORDS)


def _open_dump(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _scan_dump_tables(
    dump_path: str,
    tables: Set[str],
) -> Dict[str, List[str]]:
    """Collect INSERT statements for selected tables in one dump scan."""
    lines: Dict[str, List[str]] = {table: [] for table in tables}
    started = time.time()
    bytes_read = 0
    prefix = "INSERT INTO `"

    with _open_dump(dump_path) as handle:
        for raw_line in handle:
            bytes_read += len(raw_line)
            if not raw_line.startswith(prefix):
                continue
            match = re.match(r"INSERT INTO `([^`]+)`", raw_line)
            if not match:
                continue
            table = match.group(1)
            if table in tables:
                lines[table].append(raw_line)
            if bytes_read and bytes_read % (5_000_000_000) < len(raw_line):
                elapsed = time.time() - started
                print(
                    f"  scanned {bytes_read / 1_000_000_000:.1f} GB "
                    f"({elapsed:.0f}s)...",
                    flush=True,
                )

    elapsed = time.time() - started
    print(f"Dump scan finished in {elapsed:.1f}s", flush=True)
    for table in sorted(tables):
        print(f"  {table}: {len(lines[table])} INSERT chunk(s)", flush=True)
    return lines


def _load_metadata(
    target_genes: Set[str],
    collected: Dict[str, List[str]],
) -> Tuple[
    Dict[int, dict],
    Dict[int, str],
    Dict[int, int],
    Set[int],
    Set[int],
]:
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

    entrez_to_symbol: Dict[int, str] = {}
    target_entrez: Set[int] = set()
    for line in collected["gene"]:
        for row in iter_insert_rows(line, "gene"):
            if len(row) < 2:
                continue
            entrez = row[0]
            symbol = str(row[1] or "").upper()
            if not isinstance(entrez, int) or entrez <= 0:
                continue
            entrez_to_symbol[entrez] = symbol
            if symbol in target_genes:
                target_entrez.add(entrez)

    mutation_profiles: Set[int] = set()
    profile_to_study: Dict[int, int] = {}
    for line in collected["genetic_profile"]:
        for row in iter_insert_rows(line, "genetic_profile"):
            if len(row) < 4:
                continue
            profile_id = int(row[0])
            study_int = int(row[2])
            alteration_type = str(row[3] or "")
            if study_int not in studies:
                continue
            if alteration_type == "MUTATION_EXTENDED":
                mutation_profiles.add(profile_id)
                profile_to_study[profile_id] = study_int

    print(
        f"  NSCLC studies: {len(studies)} | target genes: {len(target_entrez)} | "
        f"mutation profiles: {len(mutation_profiles)}",
        flush=True,
    )
    return studies, entrez_to_symbol, profile_to_study, mutation_profiles, target_entrez


def _load_patients_and_samples(
    collected: Dict[str, List[str]],
    studies: Dict[int, dict],
) -> Tuple[Dict[int, Tuple[str, str]], Dict[int, int]]:
    patient_rows: Dict[int, Tuple[str, str]] = {}
    for line in collected["patient"]:
        for row in iter_insert_rows(line, "patient"):
            if len(row) < 3:
                continue
            patient_int = int(row[0])
            study_int = int(row[2])
            if study_int not in studies:
                continue
            study_id = studies[study_int]["study_id"]
            patient_rows[patient_int] = (study_id, str(row[1]))

    sample_to_patient: Dict[int, int] = {}
    for line in collected["sample"]:
        for row in iter_insert_rows(line, "sample"):
            if len(row) < 4:
                continue
            sample_int = int(row[0])
            patient_int = int(row[3])
            if patient_int in patient_rows:
                sample_to_patient[sample_int] = patient_int

    print(
        f"  NSCLC patients: {len(patient_rows)} | samples: {len(sample_to_patient)}",
        flush=True,
    )
    return patient_rows, sample_to_patient


def _load_mutation_events(
    collected: Dict[str, List[str]],
    target_entrez: Set[int],
) -> Dict[int, str]:
    event_protein: Dict[int, str] = {}
    for line in collected["mutation_event"]:
        for row in iter_insert_rows(line, "mutation_event"):
            if len(row) < 8:
                continue
            event_id = row[0]
            entrez = row[1]
            if not isinstance(event_id, int) or not isinstance(entrez, int):
                continue
            if entrez not in target_entrez:
                continue
            protein_change = row[7]
            event_protein[event_id] = None if protein_change is None else str(protein_change)

    print(f"  mutation events kept: {len(event_protein)}", flush=True)
    return event_protein


def _load_mutations(
    collected: Dict[str, List[str]],
    patient_rows: Dict[int, Tuple[str, str]],
    sample_to_patient: Dict[int, int],
    mutation_profiles: Set[int],
    target_entrez: Set[int],
    entrez_to_symbol: Dict[int, str],
    event_protein: Dict[int, str],
) -> List[tuple]:
    rows: List[tuple] = []
    seen: Set[tuple] = set()
    for line in collected["mutation"]:
        for row in iter_insert_rows(line, "mutation"):
            if len(row) < 4:
                continue
            event_id = row[0]
            profile_id = row[1]
            sample_id = row[2]
            entrez = row[3]
            if (
                not isinstance(event_id, int)
                or not isinstance(profile_id, int)
                or not isinstance(sample_id, int)
                or not isinstance(entrez, int)
            ):
                continue
            if profile_id not in mutation_profiles or entrez not in target_entrez:
                continue
            patient_int = sample_to_patient.get(sample_id)
            if patient_int is None or patient_int not in patient_rows:
                continue

            study_id, stable_id = patient_rows[patient_int]
            gene_symbol = entrez_to_symbol.get(entrez, str(entrez))
            protein_change = event_protein.get(event_id)
            dedupe_key = (study_id, stable_id, gene_symbol, protein_change)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            rows.append((study_id, stable_id, gene_symbol, protein_change, entrez))

    print(f"  mutation rows kept: {len(rows)}", flush=True)
    return rows


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def clear_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE patient_mutations, patients, studies")
    conn.commit()


def load_rows_to_neon(
    conn,
    studies: Dict[int, dict],
    patient_rows: Dict[int, Tuple[str, str]],
    mutation_rows: List[tuple],
) -> None:
    study_values = [
        (meta["study_id"], meta["study_name"], meta["cancer_type_id"])
        for meta in studies.values()
    ]
    patient_values = [
        (study_id, stable_id, stable_id)
        for _, (study_id, stable_id) in patient_rows.items()
    ]

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO studies (study_id, study_name, cancer_type_id)
            VALUES %s
            ON CONFLICT (study_id) DO UPDATE SET
                study_name = EXCLUDED.study_name,
                cancer_type_id = EXCLUDED.cancer_type_id
            """,
            study_values,
        )
        execute_values(
            cur,
            """
            INSERT INTO patients (study_id, patient_id, stable_id)
            VALUES %s
            ON CONFLICT (study_id, patient_id) DO UPDATE SET
                stable_id = EXCLUDED.stable_id
            """,
            patient_values,
            page_size=5000,
        )
        execute_values(
            cur,
            """
            INSERT INTO patient_mutations (
                study_id, patient_id, gene_symbol, protein_change, entrez_gene_id
            )
            VALUES %s
            ON CONFLICT (study_id, patient_id, gene_symbol, protein_change) DO NOTHING
            """,
            mutation_rows,
            page_size=5000,
        )
    conn.commit()


def summarize_gene_counts(conn, genes: Iterable[str]) -> None:
    gene_list = sorted(set(genes))
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM patients")
        total_patients = cur.fetchone()[0]
        cur.execute(
            """
            SELECT gene_symbol, COUNT(DISTINCT study_id || '::' || patient_id) AS patient_count
            FROM patient_mutations
            WHERE gene_symbol = ANY(%s)
            GROUP BY gene_symbol
            ORDER BY patient_count DESC, gene_symbol
            """,
            (gene_list,),
        )
        rows = cur.fetchall()
    print("\nLoaded NSCLC patient counts by gene:")
    print(f"  total NSCLC patients: {total_patients}")
    for gene_symbol, patient_count in rows:
        without = total_patients - patient_count
        print(
            f"  {gene_symbol}: {patient_count} with mutation, "
            f"{without} without"
        )


def main(argv: List[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Load NSCLC-only cBioPortal genes/patients from a MySQL dump into Neon Postgres."
        )
    )
    parser.add_argument(
        "--dump",
        default="/Users/qiwang/Downloads/dump_2026_04_18_v2_14_5.sql.gz",
        help="Path to cBioPortal MySQL dump (.sql or .sql.gz)",
    )
    parser.add_argument(
        "--genes",
        default=",".join(sorted(DEFAULT_NSCLC_GENES)),
        help="Comma-separated HUGO gene symbols to load",
    )
    parser.add_argument(
        "--skip-clear",
        action="store_true",
        help="Do not truncate existing NSCLC tables before loading",
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.dump):
        print(f"Dump file not found: {args.dump}", file=sys.stderr)
        return 1

    target_genes = {
        gene.strip().upper()
        for gene in args.genes.split(",")
        if gene.strip()
    }
    if not target_genes:
        print("No genes specified.", file=sys.stderr)
        return 1

    print(f"Loading NSCLC data from: {args.dump}", flush=True)
    print(f"Target genes: {', '.join(sorted(target_genes))}", flush=True)

    tables = {
        "type_of_cancer",
        "cancer_study",
        "gene",
        "genetic_profile",
        "patient",
        "sample",
        "mutation_event",
        "mutation",
    }
    print("Scanning dump once for NSCLC-related tables...", flush=True)
    collected = _scan_dump_tables(args.dump, tables)

    studies, entrez_to_symbol, profile_to_study, mutation_profiles, target_entrez = (
        _load_metadata(target_genes, collected)
    )
    if not studies:
        print("No NSCLC studies found in dump.", file=sys.stderr)
        return 1
    if not target_entrez:
        print("None of the target genes were found in the dump gene table.", file=sys.stderr)
        return 1

    patient_rows, sample_to_patient = _load_patients_and_samples(collected, studies)
    event_protein = _load_mutation_events(collected, target_entrez)
    mutation_rows = _load_mutations(
        collected,
        patient_rows,
        sample_to_patient,
        mutation_profiles,
        target_entrez,
        entrez_to_symbol,
        event_protein,
    )

    conn = psycopg2.connect(get_database_url())
    try:
        init_schema(conn)
        if not args.skip_clear:
            clear_tables(conn)
        load_rows_to_neon(conn, studies, patient_rows, mutation_rows)
        summarize_gene_counts(conn, target_genes)
    finally:
        conn.close()

    print("\nDone. Neon Postgres now contains NSCLC studies, patients, and gene mutations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
