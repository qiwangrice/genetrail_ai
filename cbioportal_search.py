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


def _normalize_biomarker_genes(biomarkers: Iterable[str]) -> List[str]:
    """Reduce biomarker strings to unique HUGO gene symbols (no variant detail)."""
    seen: Set[str] = set()
    normalized: List[str] = []
    for biomarker in biomarkers:
        if not biomarker:
            continue
        gene = _biomarker_gene(biomarker)
        if gene in seen:
            continue
        seen.add(gene)
        normalized.append(gene)
    return normalized


def _patient_has_gene(mutations: List[dict], gene: str) -> bool:
    target = gene.upper()
    return any(mutation["gene_symbol"].upper() == target for mutation in mutations)


def _patient_matches_all_biomarkers(
    mutations: List[dict],
    biomarkers: Iterable[str],
) -> bool:
    genes = _normalize_biomarker_genes(biomarkers)
    if not genes:
        return True
    return all(_patient_has_gene(mutations, gene) for gene in genes)


def _patient_matches_any_biomarker(
    mutations: List[dict],
    biomarkers: Iterable[str],
) -> bool:
    genes = _normalize_biomarker_genes(biomarkers)
    if not genes:
        return False
    return any(_patient_has_gene(mutations, gene) for gene in genes)


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


def _ensure_metadata_tables(cur) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS table_count
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'patient_metadata'
        """
    )
    if cur.fetchone()["table_count"] < 1:
        raise RuntimeError(
            "Neon Postgres is missing patient metadata. Run:\n"
            "  poetry run python database/load_nsclc_patient_metadata_to_neon.py"
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


def _fetch_metadata_by_patient(cur, patient_keys: List[PatientKey]) -> Dict[PatientKey, dict]:
    if not patient_keys:
        return {}

    cur.execute(
        """
        SELECT
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
        FROM patient_metadata
        WHERE (study_id, patient_id) IN %s
        """,
        (tuple(patient_keys),),
    )
    return {(row["study_id"], row["patient_id"]): row for row in cur.fetchall()}


def _empty_metadata_search_result(
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
        "patients_with_metadata": 0,
        "metadata_coverage": {},
        "age_summary": {},
        "sex_distribution": [],
        "race_distribution": [],
        "ethnicity_distribution": [],
        "smoking_status_distribution": [],
        "stage_distribution": [],
        "ecog_status_distribution": [],
        "patients_with_os_status": 0,
        "sex_by_os_status": [],
        "race_by_os_status": [],
        "ethnicity_by_os_status": [],
        "smoking_status_by_os_status": [],
        "stage_by_os_status": [],
        "ecog_status_by_os_status": [],
        "age_by_os_status": [],
        "patients": [],
    }


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
    raw = _summarize_categorical_distribution(patients, "os_status", label_key="status")
    if not raw:
        return []

    by_status = {row["status"]: row for row in raw}
    ordered: List[dict] = []
    for status in OS_STATUS_ORDER:
        row = by_status.get(status)
        if row and row["count"] > 0:
            ordered.append(row)
    for row in raw:
        if row["status"] not in OS_STATUS_ORDER:
            ordered.append(row)
    return ordered


def _summarize_categorical_distribution(
    patients: Iterable[dict],
    field: str,
    *,
    label_key: str = "value",
    missing_label: str = "Unknown",
    include_missing: bool = True,
) -> List[dict]:
    counts: Dict[str, int] = {}
    for patient in patients:
        value = patient.get(field)
        if value is None or not str(value).strip():
            if not include_missing:
                continue
            label = missing_label
        else:
            label = str(value).strip()
        counts[label] = counts.get(label, 0) + 1

    if not counts:
        return []

    total = sum(counts.values()) or 1
    return [
        {
            label_key: label,
            "count": count,
            "percentage": round(100 * count / total, 1),
        }
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


RACE_CATEGORY_ORDER = (
    "White",
    "Black or African American",
    "Asian",
    "Other",
)

SMOKING_CATEGORY_ORDER = ("Yes", "No")

STAGE_CATEGORY_ORDER = ("I", "II", "III", "IV", "Other")

OS_STATUS_ORDER = ("Living", "Deceased", "Unknown")


def _normalize_race(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    upper = text.upper()
    if any(token in upper for token in ("WHITE", "CAUCASIAN", "EUROPEAN")):
        return "White"
    if any(
        token in upper
        for token in ("BLACK", "AFRICAN AMERICAN", "AFRICAN-AMERICAN", "AFRICAN_AMERICAN")
    ):
        return "Black or African American"
    if any(
        token in upper
        for token in ("ASIAN", "FAR EAST", "INDIAN SUBCONT", "SOUTH ASIAN", "EAST ASIAN")
    ):
        return "Asian"
    return "Other"


def _normalize_smoking_status(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    upper = text.upper()
    if upper in {"YES", "Y", "TRUE", "T", "1"}:
        return "Yes"
    if upper in {"NO", "N", "FALSE", "F", "0"}:
        return "No"

    if any(
        token in upper
        for token in (
            "NEVER",
            "NON-SMOKER",
            "NON SMOKER",
            "NONSMOKER",
            "LIFELONG NON",
            "LIFETIME NON",
            "LESS THAN 100 CIGARETTES",
        )
    ):
        return "No"

    if any(
        token in upper
        for token in (
            "SMOKER",
            "SMOKING",
            "SMOKE",
            "CURRENT",
            "FORMER",
            "EVER",
            "EX-SMOKER",
            "EX SMOKER",
            "HEAVY",
            "LIGHT",
            "REFORMED",
            "CIGARETTE",
            "TOBACCO",
        )
    ):
        return "Yes"

    return None


def _normalize_stage(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    compact = re.sub(r"[^A-Z0-9]", "", text.upper())
    compact = compact.removeprefix("STAGE")
    if compact.startswith("IV"):
        return "IV"
    if compact.startswith("III") or "III" in compact:
        return "III"
    if compact.startswith("II") or "II" in compact:
        return "II"
    if compact.startswith("I") or compact in {"IA", "IB"}:
        return "I"
    return "Other"


def _apply_metadata_category_normalization(patient: dict) -> dict:
    normalized = dict(patient)
    if normalized.get("race") is not None:
        normalized["race"] = _normalize_race(normalized["race"])
    if normalized.get("smoking_status") is not None:
        normalized["smoking_status"] = _normalize_smoking_status(
            normalized["smoking_status"]
        )
    if normalized.get("stage") is not None:
        normalized["stage"] = _normalize_stage(normalized["stage"])
    return normalized


def _sort_attribute_rows(
    rows: List[dict],
    *,
    label_key: str,
    category_order: Tuple[str, ...],
) -> List[dict]:
    order_index = {label: index for index, label in enumerate(category_order)}

    def sort_key(row: dict) -> tuple:
        label = row.get(label_key) or ""
        size = row.get("patient_count", row.get("count", 0))
        return (order_index.get(label, len(category_order)), -size, label)

    return sorted(rows, key=sort_key)


def _sort_distribution_rows(
    rows: List[dict],
    category_order: Tuple[str, ...],
) -> List[dict]:
    return _sort_attribute_rows(rows, label_key="value", category_order=category_order)


def _summarize_age(patients: Iterable[dict]) -> dict:
    ages = [
        int(patient["age"])
        for patient in patients
        if patient.get("age") is not None
    ]
    if not ages:
        return {}

    return {
        "count": len(ages),
        "average": round(sum(ages) / len(ages), 1),
        "min": min(ages),
        "max": max(ages),
        "distribution": _summarize_os_days(
            [{"os_days": age} for age in ages],
            bin_count=6,
        ),
    }


def _summarize_attribute_by_os_status(
    patients: Iterable[dict],
    field: str,
    *,
    label_key: str = "value",
    max_categories: int = 8,
) -> List[dict]:
    groups: Dict[str, List[dict]] = {}
    for patient in patients:
        value = patient.get(field)
        if value is None or not str(value).strip():
            continue
        label = str(value).strip()
        groups.setdefault(label, []).append(patient)

    rows = [
        {
            label_key: label,
            "patient_count": len(group_patients),
            "os_status_distribution": _summarize_os_status(group_patients),
        }
        for label, group_patients in sorted(
            groups.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
    ]
    return rows[:max_categories]


def _summarize_age_by_os_status(
    patients: Iterable[dict],
    bin_count: int = 6,
) -> List[dict]:
    patients_with_age = [
        patient
        for patient in patients
        if patient.get("age") is not None
    ]
    if not patients_with_age:
        return []

    ages = [int(patient["age"]) for patient in patients_with_age]
    min_age = min(ages)
    max_age = max(ages)
    if min_age == max_age:
        return [
            {
                "label": f"{min_age} years",
                "patient_count": len(patients_with_age),
                "os_status_distribution": _summarize_os_status(patients_with_age),
            }
        ]

    step = (max_age - min_age) / bin_count
    buckets: List[List[dict]] = [[] for _ in range(bin_count)]
    labels: List[str] = []
    for index in range(bin_count):
        start = min_age + index * step
        end = max_age if index == bin_count - 1 else min_age + (index + 1) * step
        labels.append(f"{int(round(start))}–{int(round(end))} yrs")

    for patient in patients_with_age:
        age = int(patient["age"])
        if step <= 0:
            bucket_index = 0
        else:
            bucket_index = min(int((age - min_age) / step), bin_count - 1)
        buckets[bucket_index].append(patient)

    return [
        {
            "label": labels[index],
            "patient_count": len(buckets[index]),
            "os_status_distribution": _summarize_os_status(buckets[index]),
        }
        for index in range(bin_count)
        if buckets[index]
    ]


def _metadata_by_os_status_summaries(patients: Iterable[dict]) -> dict:
    patient_list = list(patients)
    return {
        "sex_by_os_status": _summarize_attribute_by_os_status(patient_list, "sex"),
        "race_by_os_status": _sort_attribute_rows(
            _summarize_attribute_by_os_status(
                patient_list,
                "race",
                max_categories=len(RACE_CATEGORY_ORDER),
            ),
            label_key="value",
            category_order=RACE_CATEGORY_ORDER,
        ),
        "ethnicity_by_os_status": _summarize_attribute_by_os_status(
            patient_list, "ethnicity"
        ),
        "smoking_status_by_os_status": _sort_attribute_rows(
            _summarize_attribute_by_os_status(
                patient_list,
                "smoking_status",
                max_categories=len(SMOKING_CATEGORY_ORDER),
            ),
            label_key="value",
            category_order=SMOKING_CATEGORY_ORDER,
        ),
        "stage_by_os_status": _sort_attribute_rows(
            _summarize_attribute_by_os_status(
                patient_list,
                "stage",
                max_categories=len(STAGE_CATEGORY_ORDER),
            ),
            label_key="value",
            category_order=STAGE_CATEGORY_ORDER,
        ),
        "ecog_status_by_os_status": _summarize_attribute_by_os_status(
            patient_list, "ecog_status"
        ),
        "age_by_os_status": _summarize_age_by_os_status(patient_list),
    }


def _metadata_coverage(patients: Iterable[dict]) -> dict:
    fields = (
        "age",
        "sex",
        "race",
        "ethnicity",
        "smoking_status",
        "stage",
        "ecog_status",
        "height_cm",
        "weight_kg",
        "bmi",
        "country",
    )
    patient_list = list(patients)
    total = len(patient_list) or 1
    coverage: Dict[str, dict] = {}
    for field in fields:
        count = sum(1 for patient in patient_list if patient.get(field) is not None)
        coverage[field] = {
            "count": count,
            "percentage": round(100 * count / total, 1),
        }
    return coverage


def _resolve_matched_patient_keys(
    cur,
    result,
    genes: Iterable[str] | None,
) -> Tuple[List[str], List[str], List[str], int, List[PatientKey]]:
    required = _normalize_biomarker_genes(result.required_biomarkers or [])
    excluded = _normalize_biomarker_genes(result.excluded_biomarkers or [])
    prior = result.prior_treatments or []
    gene_filter = sorted(
        set(genes or DEFAULT_NSCLC_GENES)
        | set(required)
        | set(excluded)
    )

    patient_keys, mutations_by_patient = _fetch_mutations_and_patients(cur, gene_filter)
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
        return required, excluded, prior, biomarker_eligible_count, []

    if prior:
        treatments_by_patient = _fetch_treatments_by_patient(cur, biomarker_eligible_keys)
        matched_keys = sorted(
            patient_key
            for patient_key in biomarker_eligible_keys
            if _patient_matches_prior_treatments(
                treatments_by_patient.get(patient_key, []),
                prior,
            )
        )
    else:
        matched_keys = biomarker_eligible_keys

    return required, excluded, prior, biomarker_eligible_count, matched_keys


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
    required = _normalize_biomarker_genes(result.required_biomarkers or [])
    excluded = _normalize_biomarker_genes(result.excluded_biomarkers or [])
    gene_filter = sorted(
        set(genes or DEFAULT_NSCLC_GENES)
        | set(required)
        | set(excluded)
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
    required = _normalize_biomarker_genes(result.required_biomarkers or [])
    excluded = _normalize_biomarker_genes(result.excluded_biomarkers or [])
    prior = result.prior_treatments or []
    gene_filter = sorted(
        set(genes or DEFAULT_NSCLC_GENES)
        | set(required)
        | set(excluded)
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


def search_neon_for_patient_metadata(
    result,
    genes: Iterable[str] | None = None,
    limit: int | None = None,
) -> dict:
    """
    Return demographic/clinical metadata for patients matching trial criteria.

    Expects `result` to be a SimpleNamespace (or any object) with
    required_biomarkers, excluded_biomarkers, and optional prior_treatments.
    Uses the same biomarker and prior-treatment matching rules as
    search_neon_for_treatments. Requires patient_metadata from
    load_nsclc_patient_metadata_to_neon.py.
    """
    conn = psycopg2.connect(get_database_url())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _ensure_neon_tables(cur)
            _ensure_metadata_tables(cur)
            _ensure_clinical_tables(cur)
            prior = result.prior_treatments or []

            required, excluded, prior, biomarker_eligible_count, matched_keys = (
                _resolve_matched_patient_keys(cur, result, genes)
            )

            if not biomarker_eligible_count:
                return _empty_metadata_search_result(required, excluded, prior)

            if not matched_keys:
                return _empty_metadata_search_result(
                    required,
                    excluded,
                    prior,
                    biomarker_eligible_count=biomarker_eligible_count,
                )

            all_metadata_rows = _fetch_metadata_by_patient(cur, matched_keys)
            all_survival_rows = _fetch_survival_by_patient(cur, matched_keys)
            summary_patients = []
            normalized_by_key: Dict[PatientKey, dict] = {}
            for study_id, patient_id in matched_keys:
                key = (study_id, patient_id)
                metadata = dict(all_metadata_rows.get(key, {}))
                survival = all_survival_rows.get(key, {})
                patient = _apply_metadata_category_normalization(
                    {
                        **metadata,
                        "study_id": study_id,
                        "patient_id": patient_id,
                        "os_status": survival.get("os_status"),
                        "os_days": survival.get("os_days"),
                    }
                )
                summary_patients.append(patient)
                normalized_by_key[key] = patient
            patients_with_metadata = sum(
                1
                for patient in summary_patients
                if any(
                    patient.get(field) is not None
                    for field in (
                        "age",
                        "sex",
                        "race",
                        "ethnicity",
                        "smoking_status",
                        "stage",
                        "ecog_status",
                        "height_cm",
                        "weight_kg",
                        "bmi",
                        "country",
                    )
                )
            )
            patients_with_os_status = sum(
                1 for patient in summary_patients if patient.get("os_status") is not None
            )
            by_os_status = _metadata_by_os_status_summaries(summary_patients)

            selected_keys = matched_keys
            if limit is not None and limit >= 0:
                selected_keys = matched_keys[:limit]
    finally:
        conn.close()

    patients = []
    for study_id, patient_id in selected_keys:
        key = (study_id, patient_id)
        normalized = normalized_by_key.get(key, {})
        patients.append(
            {
                "study_id": study_id,
                "patient_id": patient_id,
                "os_status": normalized.get("os_status"),
                "os_days": normalized.get("os_days"),
                "age": normalized.get("age"),
                "sex": normalized.get("sex"),
                "race": normalized.get("race"),
                "ethnicity": normalized.get("ethnicity"),
                "smoking_status": normalized.get("smoking_status"),
                "stage": normalized.get("stage"),
                "ecog_status": normalized.get("ecog_status"),
                "height_cm": normalized.get("height_cm"),
                "weight_kg": normalized.get("weight_kg"),
                "bmi": normalized.get("bmi"),
                "country": normalized.get("country"),
            }
        )

    return {
        "data_source": "neon_postgres",
        "required_biomarkers": required,
        "excluded_biomarkers": excluded,
        "prior_treatments": prior,
        "biomarker_eligible_count": biomarker_eligible_count,
        "prior_treatment_matched_count": len(matched_keys),
        "eligible_patient_count": len(matched_keys),
        "patients_with_metadata": patients_with_metadata,
        "patients_with_os_status": patients_with_os_status,
        "metadata_coverage": _metadata_coverage(summary_patients),
        "age_summary": _summarize_age(summary_patients),
        "sex_distribution": _summarize_categorical_distribution(
            summary_patients, "sex", include_missing=False
        ),
        "race_distribution": _sort_distribution_rows(
            _summarize_categorical_distribution(
                summary_patients, "race", include_missing=False
            ),
            RACE_CATEGORY_ORDER,
        ),
        "ethnicity_distribution": _summarize_categorical_distribution(
            summary_patients, "ethnicity", include_missing=False
        ),
        "smoking_status_distribution": _sort_distribution_rows(
            _summarize_categorical_distribution(
                summary_patients, "smoking_status", include_missing=False
            ),
            SMOKING_CATEGORY_ORDER,
        ),
        "stage_distribution": _sort_distribution_rows(
            _summarize_categorical_distribution(
                summary_patients, "stage", include_missing=False
            ),
            STAGE_CATEGORY_ORDER,
        ),
        "ecog_status_distribution": _summarize_categorical_distribution(
            summary_patients, "ecog_status", include_missing=False
        ),
        **by_os_status,
        "patients_returned": len(patients),
        "patients": patients,
    }


if __name__ == "__main__":
    import json
    from types import SimpleNamespace

    demo = SimpleNamespace(
        cancer_type="metastatic non-small cell lung cancer",
        required_biomarkers=["KRAS G12C mutation"],
        excluded_biomarkers=["EGFR activating mutations", "ALK fusions"],
        prior_treatments=["platinum-based chemotherapy"],
    )
    print(json.dumps(search_cbioportal_for_patients(demo), indent=2))
    print()
    print(json.dumps(search_neon_for_treatments(demo, limit=5), indent=2))
    print()
    print(json.dumps(search_neon_for_patient_metadata(demo, limit=5), indent=2))
