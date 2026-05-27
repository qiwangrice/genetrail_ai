from __future__ import annotations

import os
import re
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

TREATMENT_KEYWORD_EXPANSIONS = {
    "platinum": ["platinum", "cisplatin", "carboplatin", "oxaliplatin", "carbo"],
    "chemotherapy": ["chemotherapy", "chemo", "cytotoxic"],
    "immunotherapy": ["immunotherapy", "pembrolizumab", "nivolumab", "atezolizumab", "durvalumab", "ipilimumab", "pembro", "nivo"],
    "radiation": ["radiation", "radiotherapy", "rt"],
    "pemetrexed": ["pemetrexed", "alimta", "pem"],
    "taxane": ["taxane", "paclitaxel", "docetaxel", "taxol", "taxotere"],
    "targeted": ["targeted", "tyrosine kinase", "tki", "osimertinib", "erlotinib", "gefitinib", "afatinib"],
    "bevacizumab": ["bevacizumab", "avastin"],
}

NEGATIVE_TREATMENT_VALUES = {
    "not performed",
    "none",
    "no",
    "na",
    "n/a",
    "unknown",
    "not applicable",
    "not received",
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
            "  poetry run python database/load_nsclc_to_neon.py"
        )


def _ensure_clinical_tables(cur) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS table_count
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN ('patient_survival', 'patient_treatments')
        """
    )
    if cur.fetchone()["table_count"] < 2:
        raise RuntimeError(
            "Neon Postgres is missing clinical tables. Run:\n"
            "  poetry run python database/load_nsclc_clinical_to_neon.py"
        )


def _eligible_patient_keys(
    patient_keys: Set[PatientKey],
    mutations_by_patient: Dict[PatientKey, List[dict]],
    required: Iterable[str],
    excluded: Iterable[str],
) -> Set[PatientKey]:
    eligible: Set[PatientKey] = set()
    for patient_key in patient_keys:
        mutations = mutations_by_patient.get(patient_key, [])
        has_required = _patient_matches_all_biomarkers(mutations, required)
        has_excluded = _patient_matches_any_biomarker(mutations, excluded)
        if has_required and not has_excluded:
            eligible.add(patient_key)
    return eligible


def _fetch_mutations_and_patients(
    cur,
    gene_filter: List[str],
) -> Tuple[Set[PatientKey], Dict[PatientKey, List[dict]]]:
    cur.execute(
        """
        SELECT study_id, patient_id
        FROM patients
        """
    )
    patient_keys = {(row["study_id"], row["patient_id"]) for row in cur.fetchall()}

    cur.execute(
        """
        SELECT study_id, patient_id, gene_symbol, protein_change
        FROM patient_mutations
        WHERE gene_symbol = ANY(%s)
        """,
        (gene_filter,),
    )
    mutation_rows = cur.fetchall()

    mutations_by_patient: Dict[PatientKey, List[dict]] = {}
    for row in mutation_rows:
        key = (row["study_id"], row["patient_id"])
        mutations_by_patient.setdefault(key, []).append(row)

    return patient_keys, mutations_by_patient


def _treatment_search_terms(prior_treatment: str) -> List[str]:
    text = prior_treatment.lower().strip()
    terms = {text}
    for word in re.findall(r"[a-z0-9]+", text):
        if len(word) >= 4:
            terms.add(word)
    for keyword, expansions in TREATMENT_KEYWORD_EXPANSIONS.items():
        if keyword in text:
            terms.update(expansions)
    return sorted(terms)


def _treatment_value_is_negative(value: str) -> bool:
    normalized = value.lower().strip()
    return (
        normalized in NEGATIVE_TREATMENT_VALUES
        or normalized.startswith("not ")
        or normalized.startswith("no ")
    )


def _treatment_value_is_informative(value: str) -> bool:
    normalized = value.strip()
    if not normalized or _treatment_value_is_negative(normalized):
        return False
    if normalized.isdigit():
        return False
    return True


def _treatment_matches_prior(prior_treatment: str, patient_treatments: List[dict]) -> bool:
    terms = _treatment_search_terms(prior_treatment)
    for treatment in patient_treatments:
        value = str(treatment.get("value") or "").strip()
        if not _treatment_value_is_informative(value):
            continue
        haystack = " ".join(
            [
                value,
                str(treatment.get("attribute_label") or ""),
            ]
        ).lower()
        if any(term in haystack for term in terms):
            return True
    return False


def _patient_matches_prior_treatments(
    patient_treatments: List[dict],
    prior_treatments: Iterable[str],
) -> bool:
    prior_list = [item for item in prior_treatments if item and str(item).strip()]
    if not prior_list:
        return True
    if not patient_treatments:
        return False
    return all(
        _treatment_matches_prior(prior_treatment, patient_treatments)
        for prior_treatment in prior_list
    )


def _fetch_treatments_by_patient(cur, patient_keys: List[PatientKey]) -> Dict[PatientKey, List[dict]]:
    if not patient_keys:
        return {}

    cur.execute(
        """
        SELECT study_id, patient_id, attribute_id, attribute_label, treatment_value
        FROM patient_treatments
        WHERE (study_id, patient_id) IN %s
        ORDER BY study_id, patient_id, attribute_id, treatment_value
        """,
        (tuple(patient_keys),),
    )
    treatments_by_patient: Dict[PatientKey, List[dict]] = {}
    for row in cur.fetchall():
        key = (row["study_id"], row["patient_id"])
        treatments_by_patient.setdefault(key, []).append(
            {
                "attribute_id": row["attribute_id"],
                "attribute_label": row["attribute_label"],
                "value": row["treatment_value"],
            }
        )
    return treatments_by_patient


def _fetch_survival_by_patient(cur, patient_keys: List[PatientKey]) -> Dict[PatientKey, dict]:
    if not patient_keys:
        return {}

    cur.execute(
        """
        SELECT study_id, patient_id, os_status, os_days
        FROM patient_survival
        WHERE (study_id, patient_id) IN %s
        """,
        (tuple(patient_keys),),
    )
    return {(row["study_id"], row["patient_id"]): row for row in cur.fetchall()}


def _empty_treatment_search_result(
    required: List[str],
    excluded: List[str],
    prior: List[str],
    biomarker_eligible_count: int = 0,
) -> dict:
    return {
        "data_source": "neon_postgres",
        "required_biomarkers": required,
        "excluded_biomarkers": excluded,
        "prior_treatments": prior,
        "biomarker_eligible_count": biomarker_eligible_count,
        "prior_treatment_matched_count": 0,
        "eligible_patient_count": 0,
        "patients_returned": 0,
        "patients_with_os_status": 0,
        "patients_with_os_days": 0,
        "os_status_distribution": [],
        "os_days_distribution": [],
        "patients": [],
    }


def _summarize_os_status(patients: Iterable[dict]) -> List[dict]:
    counts: Dict[str, int] = {}
    for patient in patients:
        status = patient.get("os_status") or "Unknown"
        counts[status] = counts.get(status, 0) + 1

    total = sum(counts.values()) or 1
    return [
        {
            "status": status,
            "count": count,
            "percentage": round(100 * count / total, 1),
        }
        for status, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _summarize_os_days(patients: Iterable[dict], bin_count: int = 6) -> List[dict]:
    values = [
        float(patient["os_days"])
        for patient in patients
        if patient.get("os_days") is not None
    ]
    if not values:
        return []

    min_days = min(values)
    max_days = max(values)
    if min_days == max_days:
        return [
            {
                "label": f"{int(round(min_days))} days",
                "min_days": min_days,
                "max_days": max_days,
                "count": len(values),
            }
        ]

    step = (max_days - min_days) / bin_count
    buckets = [0] * bin_count
    labels: List[dict] = []

    for index in range(bin_count):
        start = min_days + index * step
        end = max_days if index == bin_count - 1 else min_days + (index + 1) * step
        labels.append(
            {
                "label": f"{int(round(start))}–{int(round(end))} days",
                "min_days": start,
                "max_days": end,
            }
        )

    for value in values:
        if step <= 0:
            bucket_index = 0
        else:
            bucket_index = min(int((value - min_days) / step), bin_count - 1)
        buckets[bucket_index] += 1

    return [{**labels[index], "count": buckets[index]} for index in range(bin_count)]


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

            patient_keys, mutations_by_patient = _fetch_mutations_and_patients(
                cur, gene_filter
            )

            gene_patient_counts = _fetch_gene_patient_counts(
                cur, gene_filter, unique_patients
            )
    finally:
        conn.close()

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


def search_neon_for_treatments(
    result,
    genes: Iterable[str] | None = None,
    limit: int | None = None,
) -> dict:
    """
    Return treatments and survival for patients matching trial criteria.

    A patient is included when they match all required_biomarkers, none of the
    excluded_biomarkers, and (when provided) all prior_treatments found in
    patient_treatments. Expects clinical tables from
    load_nsclc_clinical_to_neon.py: patient_survival, patient_treatments.
    """
    required = result.required_biomarkers or []
    excluded = result.excluded_biomarkers or []
    prior = result.prior_treatments or []
    gene_filter = sorted(
        set(genes or DEFAULT_NSCLC_GENES)
        | _biomarker_genes(required)
        | _biomarker_genes(excluded)
    )

    conn = psycopg2.connect(get_database_url())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _ensure_neon_tables(cur)
            _ensure_clinical_tables(cur)

            patient_keys, mutations_by_patient = _fetch_mutations_and_patients(
                cur, gene_filter
            )
            biomarker_eligible_keys = sorted(
                _eligible_patient_keys(
                    patient_keys,
                    mutations_by_patient,
                    required,
                    excluded,
                )
            )
            biomarker_eligible_count = len(biomarker_eligible_keys)

            if not biomarker_eligible_keys:
                return _empty_treatment_search_result(required, excluded, prior)

            if prior:
                treatments_by_patient = _fetch_treatments_by_patient(
                    cur, biomarker_eligible_keys
                )
                matched_keys = sorted(
                    patient_key
                    for patient_key in biomarker_eligible_keys
                    if _patient_matches_prior_treatments(
                        treatments_by_patient.get(patient_key, []),
                        prior,
                    )
                )
            else:
                treatments_by_patient = {}
                matched_keys = biomarker_eligible_keys

            prior_treatment_matched_count = len(matched_keys)
            if not matched_keys:
                return _empty_treatment_search_result(
                    required,
                    excluded,
                    prior,
                    biomarker_eligible_count=biomarker_eligible_count,
                )

            all_survival_rows = _fetch_survival_by_patient(cur, matched_keys)
            summary_patients = [
                {
                    "os_status": all_survival_rows.get(key, {}).get("os_status"),
                    "os_days": all_survival_rows.get(key, {}).get("os_days"),
                }
                for key in matched_keys
            ]
            os_status_distribution = _summarize_os_status(summary_patients)
            os_days_distribution = _summarize_os_days(summary_patients)
            patients_with_os_status = sum(
                1 for patient in summary_patients if patient["os_status"] is not None
            )
            patients_with_os_days = sum(
                1 for patient in summary_patients if patient["os_days"] is not None
            )

            selected_keys = matched_keys
            if limit is not None and limit >= 0:
                selected_keys = matched_keys[:limit]

            if not prior:
                treatments_by_patient = _fetch_treatments_by_patient(cur, selected_keys)
    finally:
        conn.close()

    patients = []
    for study_id, patient_id in selected_keys:
        key = (study_id, patient_id)
        survival = all_survival_rows.get(key, {})
        patients.append(
            {
                "study_id": study_id,
                "patient_id": patient_id,
                "os_status": survival.get("os_status"),
                "os_days": survival.get("os_days"),
                "treatments": treatments_by_patient.get(key, []),
            }
        )

    return {
        "data_source": "neon_postgres",
        "required_biomarkers": required,
        "excluded_biomarkers": excluded,
        "prior_treatments": prior,
        "biomarker_eligible_count": biomarker_eligible_count,
        "prior_treatment_matched_count": prior_treatment_matched_count,
        "eligible_patient_count": prior_treatment_matched_count,
        "patients_with_os_status": patients_with_os_status,
        "patients_with_os_days": patients_with_os_days,
        "os_status_distribution": os_status_distribution,
        "os_days_distribution": os_days_distribution,
        "patients_returned": len(patients),
        "patients": patients,
    }


if __name__ == "__main__":
    import json
    from types import SimpleNamespace

    demo = SimpleNamespace(
        cancer_type="metastatic non-small cell lung cancer",
        required_biomarkers=["KRAS"],
        excluded_biomarkers=["EGFR", "ALK"],
        prior_treatments=["platinum-based chemotherapy"],
    )
    print(json.dumps(search_cbioportal_for_patients(demo), indent=2))
    print()
    print(json.dumps(search_neon_for_treatments(demo, limit=5), indent=2))
