from __future__ import annotations

import os
from typing import Dict, Iterable, List, Set, Tuple

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()

PatientKey = Tuple[str, str]

DEFAULT_NSCLC_GENES = {
    "KRAS",
    "EGFR",
    "ALK",
    "BRAF",
    "MET",
    "ERBB2",
    "RET",
    "ROS1",
    "TP53",
    "STK11",
    "KEAP1",
    "PIK3CA",
    "NRAS",
    "MAP2K1",
}


def _biomarker_gene(biomarker: str) -> str:
    return biomarker.strip().upper().split()[0]


def _biomarker_genes(biomarkers: Iterable[str]) -> Set[str]:
    return {_biomarker_gene(biomarker) for biomarker in biomarkers if biomarker}


def _mutation_matches_biomarker(
    gene_symbol: str,
    protein_change: str | None,
    biomarker: str,
) -> bool:
    symbol = gene_symbol.upper()
    if symbol != _biomarker_gene(biomarker):
        return False
    parts = biomarker.upper().split()
    if len(parts) > 1:
        variant = parts[1]
        protein = (protein_change or "").upper()
        return variant in protein
    return True


def _patient_matches_all_biomarkers(
    mutations: List[dict],
    biomarkers: Iterable[str],
) -> bool:
    biomarkers = list(biomarkers)
    if not biomarkers:
        return True
    return all(
        any(
            _mutation_matches_biomarker(
                mutation["gene_symbol"],
                mutation.get("protein_change"),
                biomarker,
            )
            for mutation in mutations
        )
        for biomarker in biomarkers
    )


def _patient_matches_any_biomarker(
    mutations: List[dict],
    biomarkers: Iterable[str],
) -> bool:
    return any(
        _mutation_matches_biomarker(
            mutation["gene_symbol"],
            mutation.get("protein_change"),
            biomarker,
        )
        for biomarker in biomarkers
        for mutation in mutations
    )


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. Add your Neon Postgres connection string to .env"
        )
    return url


def _ensure_neon_tables(cur) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS table_count
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN ('studies', 'patients', 'patient_mutations')
        """
    )
    if cur.fetchone()["table_count"] < 3:
        raise RuntimeError(
            "Neon Postgres is missing NSCLC tables. Run:\n"
            "  poetry run python load_nsclc_to_neon.py"
        )


def _fetch_gene_patient_counts(cur, genes: List[str], total_patients: int) -> List[dict]:
    cur.execute(
        """
        SELECT gene_symbol,
               COUNT(DISTINCT study_id || '::' || patient_id) AS patients_with_mutation
        FROM patient_mutations
        WHERE gene_symbol = ANY(%s)
        GROUP BY gene_symbol
        ORDER BY gene_symbol
        """,
        (genes,),
    )
    counts_by_gene = {row["gene_symbol"]: row["patients_with_mutation"] for row in cur.fetchall()}

    return [
        {
            "gene": gene,
            "patients_with_mutation": counts_by_gene.get(gene, 0),
            "patients_without_mutation": total_patients - counts_by_gene.get(gene, 0),
        }
        for gene in genes
    ]


def search_cbioportal_for_patients(
    result,
    genes: Iterable[str] | None = None,
) -> dict:
    """
    Search NSCLC patient counts from Neon Postgres.

    Expects tables created by load_nsclc_to_neon.py:
      studies, patients, patient_mutations
    """
    required = result.required_biomarkers or []
    excluded = result.excluded_biomarkers or []
    gene_filter = sorted(
        set(genes or DEFAULT_NSCLC_GENES)
        | _biomarker_genes(required)
        | _biomarker_genes(excluded)
    )

    conn = psycopg2.connect(get_database_url())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _ensure_neon_tables(cur)

            cur.execute("SELECT COUNT(*) AS count FROM studies")
            studies_searched = cur.fetchone()["count"]

            cur.execute(
                """
                SELECT study_id
                FROM studies
                ORDER BY study_id
                """
            )
            study_ids = [row["study_id"] for row in cur.fetchall()]

            cur.execute("SELECT COUNT(*) AS count FROM patients")
            unique_patients = cur.fetchone()["count"]

            cur.execute(
                """
                SELECT study_id, patient_id
                FROM patients
                """
            )
            patient_keys = {
                (row["study_id"], row["patient_id"]) for row in cur.fetchall()
            }

            cur.execute(
                """
                SELECT study_id, patient_id, gene_symbol, protein_change
                FROM patient_mutations
                WHERE gene_symbol = ANY(%s)
                """,
                (gene_filter,),
            )
            mutation_rows = cur.fetchall()

            gene_patient_counts = _fetch_gene_patient_counts(
                cur, gene_filter, unique_patients
            )
    finally:
        conn.close()

    mutations_by_patient: Dict[PatientKey, List[dict]] = {}
    for row in mutation_rows:
        key = (row["study_id"], row["patient_id"])
        mutations_by_patient.setdefault(key, []).append(row)

    patients_with_required = 0
    eligible_patients = 0

    for patient_key in patient_keys:
        mutations = mutations_by_patient.get(patient_key, [])
        has_required = _patient_matches_all_biomarkers(mutations, required)
        has_excluded = _patient_matches_any_biomarker(mutations, excluded)
        if has_required:
            patients_with_required += 1
        if has_required and not has_excluded:
            eligible_patients += 1

    return {
        "cancer_type": result.cancer_type,
        "data_source": "neon_postgres",
        "studies_searched": studies_searched,
        "study_ids": study_ids,
        "genes_queried": gene_filter,
        "required_biomarkers": required,
        "excluded_biomarkers": excluded,
        "unique_patients_with_cancer_type": unique_patients,
        "patients_with_required_biomarkers": patients_with_required,
        "patients_without_required_biomarkers": unique_patients - patients_with_required,
        "eligible_patients": eligible_patients,
        "gene_patient_counts": gene_patient_counts,
    }


if __name__ == "__main__":
    import json
    from types import SimpleNamespace

    demo = SimpleNamespace(
        cancer_type="metastatic non-small cell lung cancer",
        required_biomarkers=["KRAS G12C"],
        excluded_biomarkers=["EGFR activating mutations", "ALK fusions"],
    )
    print(json.dumps(search_cbioportal_for_patients(demo), indent=2))
