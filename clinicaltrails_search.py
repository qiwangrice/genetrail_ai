from __future__ import annotations

import re
from typing import Iterable, List

import requests

CLINICALTRIALS_API_BASE = "https://clinicaltrials.gov/api/v2/studies"
ACTIVE_STATUSES = (
    "RECRUITING",
    "NOT_YET_RECRUITING",
    "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING",
)
FINISHED_STATUSES = (
    "COMPLETED",
    "TERMINATED",
    "WITHDRAWN",
    "SUSPENDED",
)
STUDY_STOPPED_STATUSES = (
    "TERMINATED",
    "WITHDRAWN",
    "SUSPENDED",
)
INCLUSION_MARKERS = (
    "INCLUSION CRITERIA",
    "INCLUSION:",
    "ELIGIBILITY CRITERIA",
)
EXCLUSION_MARKERS = (
    "EXCLUSION CRITERIA",
    "EXCLUSION:",
)
POSITIVE_INCLUSION_PATTERNS = (
    r"\bmust have\b",
    r"\brequires?\b",
    r"\bpositive for\b",
    r"\bwith documented\b",
    r"\bharboring\b",
    r"\bmutant\b",
    r"\bmutation\b",
    r"\bknown\b.{{0,40}}\bmutation\b",
)
NEGATIVE_EXCLUSION_PATTERNS = (
    r"\bexclude\b",
    r"\bexcluded\b",
    r"\bexclusion\b",
    r"\bwithout\b",
    r"\bmust not\b",
    r"\bno prior\b",
    r"\bnegative for\b",
    r"\bnot allowed\b",
    r"\bnot eligible\b",
)


def _biomarker_gene(biomarker: str) -> str:
    return biomarker.strip().upper().split()[0]


def _biomarker_variant(biomarker: str) -> str | None:
    parts = biomarker.strip().upper().split()
    return parts[1] if len(parts) > 1 else None


def _biomarker_in_text(biomarker: str, text: str) -> bool:
    haystack = text.upper()
    gene = _biomarker_gene(biomarker)
    if gene not in haystack:
        return False
    variant = _biomarker_variant(biomarker)
    if variant and variant not in haystack:
        return False
    return True


def _split_eligibility_sections(text: str) -> tuple[str, str]:
    upper = text.upper()
    exclusion_start = len(text)
    for marker in EXCLUSION_MARKERS:
        index = upper.find(marker)
        if index != -1 and index < exclusion_start:
            exclusion_start = index

    if exclusion_start < len(text):
        return text[:exclusion_start], text[exclusion_start:]
    return text, ""


def _context_window(text: str, biomarker: str, radius: int = 120) -> List[str]:
    upper = text.upper()
    gene = _biomarker_gene(biomarker)
    variant = _biomarker_variant(biomarker)
    needles = [gene]
    if variant:
        needles.append(variant)

    windows: List[str] = []
    for needle in needles:
        start = 0
        while True:
            index = upper.find(needle, start)
            if index == -1:
                break
            left = max(0, index - radius)
            right = min(len(text), index + len(needle) + radius)
            windows.append(text[left:right])
            start = index + len(needle)
    return windows


def _text_has_pattern(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _required_biomarkers_match(required: Iterable[str], text: str) -> tuple[bool, List[str]]:
    required_list = [item for item in required if item and str(item).strip()]
    if not required_list:
        return True, []
    missing = [biomarker for biomarker in required_list if not _biomarker_in_text(biomarker, text)]
    return not missing, missing


def _excluded_biomarkers_compatible(excluded: Iterable[str], text: str) -> tuple[bool, List[str]]:
    excluded_list = [item for item in excluded if item and str(item).strip()]
    if not excluded_list or not text.strip():
        return True, []

    inclusion_text, exclusion_text = _split_eligibility_sections(text)
    conflicts: List[str] = []

    for biomarker in excluded_list:
        if _biomarker_in_text(biomarker, exclusion_text):
            continue

        inclusion_windows = _context_window(inclusion_text, biomarker)
        if not inclusion_windows:
            continue

        for window in inclusion_windows:
            if _text_has_pattern(window, POSITIVE_INCLUSION_PATTERNS):
                conflicts.append(biomarker)
                break

    return not conflicts, sorted(set(conflicts))


def _condition_query(cancer_type: str | None) -> str:
    if cancer_type and cancer_type.strip():
        return cancer_type.strip()
    return "non-small cell lung cancer"


def _build_search_terms(required: Iterable[str], excluded: Iterable[str]) -> str | None:
    genes = sorted(
        {
            _biomarker_gene(biomarker)
            for biomarker in list(required) + list(excluded)
            if biomarker and str(biomarker).strip()
        }
    )
    if not genes:
        return None
    return " OR ".join(genes)


def _parse_study(study: dict) -> dict | None:
    protocol = study.get("protocolSection") or {}
    identification = protocol.get("identificationModule") or {}
    status = protocol.get("statusModule") or {}
    conditions = protocol.get("conditionsModule") or {}
    eligibility = protocol.get("eligibilityModule") or {}
    description = protocol.get("descriptionModule") or {}

    nct_id = identification.get("nctId")
    if not nct_id:
        return None

    return {
        "nct_id": nct_id,
        "title": identification.get("briefTitle") or identification.get("officialTitle"),
        "status": status.get("overallStatus"),
        "why_stopped": status.get("whyStopped"),
        "conditions": conditions.get("conditions") or [],
        "eligibility_text": eligibility.get("eligibilityCriteria") or "",
        "brief_summary": description.get("briefSummary"),
        "url": f"https://clinicaltrials.gov/study/{nct_id}",
        "has_results": bool(study.get("hasResults")),
    }


def _parse_p_value(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("<", "")
    try:
        return float(text)
    except ValueError:
        return None


def _primary_superiority_p_values(study: dict) -> list[float]:
    outcome_measures = (
        (study.get("resultsSection") or {})
        .get("outcomeMeasuresModule", {})
        .get("outcomeMeasures")
        or []
    )
    p_values: list[float] = []
    for measure in outcome_measures:
        if measure.get("type") != "PRIMARY":
            continue
        for analysis in measure.get("analyses") or []:
            inference = analysis.get("nonInferiorityType")
            if inference and inference != "SUPERIORITY":
                continue
            parsed = _parse_p_value(analysis.get("pValue"))
            if parsed is not None:
                p_values.append(parsed)
    return p_values


def _classify_finished_trial_outcome(study: dict, trial: dict) -> dict:
    status = trial.get("status") or ""
    why_stopped = trial.get("why_stopped")

    if status in STUDY_STOPPED_STATUSES:
        return {
            "outcome_category": "failed",
            "outcome_label": "Study stopped early",
            "outcome_reason": why_stopped or status,
            "primary_p_values": [],
        }

    if status != "COMPLETED":
        return {
            "outcome_category": "other",
            "outcome_label": status or "Unknown status",
            "outcome_reason": None,
            "primary_p_values": [],
        }

    if not trial.get("has_results"):
        return {
            "outcome_category": "completed_no_results",
            "outcome_label": "Completed without posted results",
            "outcome_reason": None,
            "primary_p_values": [],
        }

    p_values = _primary_superiority_p_values(study)
    if any(value < 0.05 for value in p_values):
        return {
            "outcome_category": "completed_positive",
            "outcome_label": "Primary endpoint met",
            "outcome_reason": "Primary superiority analysis p < 0.05",
            "primary_p_values": p_values,
        }

    if p_values:
        return {
            "outcome_category": "completed_negative",
            "outcome_label": "Primary endpoint not met",
            "outcome_reason": "Primary superiority analysis p >= 0.05",
            "primary_p_values": p_values,
        }

    return {
        "outcome_category": "completed_inconclusive",
        "outcome_label": "Results posted without comparative significance",
        "outcome_reason": "No primary superiority p-value available (often single-arm)",
        "primary_p_values": [],
    }


def _summarize_finished_trial_outcomes(trials: Iterable[dict]) -> dict:
    counts = {
        "completed_positive_count": 0,
        "completed_negative_count": 0,
        "completed_inconclusive_count": 0,
        "completed_no_results_count": 0,
        "study_stopped_count": 0,
    }
    for trial in trials:
        category = trial.get("outcome_category")
        if category == "completed_positive":
            counts["completed_positive_count"] += 1
        elif category == "completed_negative":
            counts["completed_negative_count"] += 1
        elif category == "completed_inconclusive":
            counts["completed_inconclusive_count"] += 1
        elif category == "completed_no_results":
            counts["completed_no_results_count"] += 1
        elif category == "failed":
            counts["study_stopped_count"] += 1

    failed_count = (
        counts["study_stopped_count"] + counts["completed_negative_count"]
    )
    return {
        **counts,
        "failed_count": failed_count,
        "completed_with_results_count": (
            counts["completed_positive_count"]
            + counts["completed_negative_count"]
            + counts["completed_inconclusive_count"]
        ),
    }


def _search_clinical_trials(
    *,
    cancer_type: str,
    required: Iterable[str],
    excluded: Iterable[str],
    statuses: Iterable[str],
    max_pages: int,
    max_results: int,
    page_size: int,
    timeout_seconds: int,
    include_outcome_classification: bool = False,
) -> dict:
    params = {
        "query.cond": cancer_type,
        "filter.overallStatus": ",".join(statuses),
        "pageSize": min(max(page_size, 1), 1000),
        "format": "json",
    }
    search_terms = _build_search_terms(required, excluded)
    if search_terms:
        params["query.term"] = search_terms

    matched_trials: List[dict] = []
    scanned_trials = 0
    pages_fetched = 0
    next_page_token: str | None = None

    while pages_fetched < max(1, max_pages) and len(matched_trials) < max_results:
        request_params = dict(params)
        if next_page_token:
            request_params["pageToken"] = next_page_token

        response = requests.get(
            CLINICALTRIALS_API_BASE,
            params=request_params,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        pages_fetched += 1

        studies = payload.get("studies") or []
        if not studies:
            break

        for study in studies:
            scanned_trials += 1
            trial = _parse_study(study)
            if trial is None:
                continue

            is_match, match_details = _trial_matches_criteria(trial, required, excluded)
            if not is_match:
                continue

            record = {
                "nct_id": trial["nct_id"],
                "title": trial["title"],
                "status": trial["status"],
                "conditions": trial["conditions"],
                "brief_summary": trial.get("brief_summary"),
                "has_results": trial.get("has_results"),
                "url": trial["url"],
                **match_details,
            }
            if include_outcome_classification:
                record.update(_classify_finished_trial_outcome(study, trial))

            matched_trials.append(record)
            if len(matched_trials) >= max_results:
                break

        next_page_token = payload.get("nextPageToken")
        if not next_page_token:
            break

    return {
        "pages_fetched": pages_fetched,
        "trials_scanned": scanned_trials,
        "matched_trials": matched_trials,
    }


def _resolve_trial_search_inputs(
    result,
    cancer_type: str | None = None,
    required_biomarkers: Iterable[str] | None = None,
    excluded_biomarkers: Iterable[str] | None = None,
) -> tuple[str, list[str], list[str]]:
    resolved_cancer_type = _condition_query(
        cancer_type if cancer_type is not None else getattr(result, "cancer_type", None)
    )
    if required_biomarkers is not None:
        required = list(required_biomarkers)
    else:
        required = list(getattr(result, "required_biomarkers", None) or [])
    if excluded_biomarkers is not None:
        excluded = list(excluded_biomarkers)
    else:
        excluded = list(getattr(result, "excluded_biomarkers", None) or [])
    return resolved_cancer_type, required, excluded


def _trial_matches_criteria(
    trial: dict,
    required: Iterable[str],
    excluded: Iterable[str],
) -> tuple[bool, dict]:
    text = trial.get("eligibility_text") or ""
    required_ok, missing_required = _required_biomarkers_match(required, text)
    excluded_ok, conflicting_excluded = _excluded_biomarkers_compatible(excluded, text)

    matched = required_ok and excluded_ok
    return matched, {
        "missing_required_biomarkers": missing_required,
        "conflicting_excluded_biomarkers": conflicting_excluded,
    }


def search_active_clinical_trials(
    result,
    max_pages: int = 5,
    max_results: int = 50,
    page_size: int = 100,
    timeout_seconds: int = 30,
) -> dict:
    """
    Search active trials on ClinicalTrials.gov that match extracted eligibility.

    Uses API v2: https://clinicaltrials.gov/data-api/api

    A trial matches when:
      - overall status is active/recruiting
      - condition matches cancer_type
      - all required_biomarkers appear in eligibility text
      - excluded_biomarkers are not required in the inclusion criteria
    """
    required = result.required_biomarkers or []
    excluded = result.excluded_biomarkers or []
    cancer_type = _condition_query(getattr(result, "cancer_type", None))

    search = _search_clinical_trials(
        cancer_type=cancer_type,
        required=required,
        excluded=excluded,
        statuses=ACTIVE_STATUSES,
        max_pages=max_pages,
        max_results=max_results,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
        include_outcome_classification=False,
    )

    return {
        "data_source": "clinicaltrials.gov_api_v2",
        "search_url": CLINICALTRIALS_API_BASE,
        "cancer_type": cancer_type,
        "active_statuses": list(ACTIVE_STATUSES),
        "required_biomarkers": required,
        "excluded_biomarkers": excluded,
        "pages_fetched": search["pages_fetched"],
        "trials_scanned": search["trials_scanned"],
        "matched_trial_count": len(search["matched_trials"]),
        "matched_trials": search["matched_trials"],
    }


def search_completed_clinical_trials(
    result=None,
    *,
    cancer_type: str | None = None,
    required_biomarkers: Iterable[str] | None = None,
    excluded_biomarkers: Iterable[str] | None = None,
    max_pages: int = 5,
    max_results: int = 50,
    page_size: int = 100,
    timeout_seconds: int = 30,
) -> dict:
    """
    Search finished trials on ClinicalTrials.gov that match disease and biomarkers.

    Includes COMPLETED trials plus TERMINATED/WITHDRAWN/SUSPENDED studies for
    outcome statistics. Classifies each matched trial as completed-positive,
    completed-negative, completed-inconclusive, completed-no-results, or failed
    (stopped early).

    Positive/negative classification uses posted primary superiority p-values
    when available (p < 0.05 => positive). Single-arm trials often land in
    completed_inconclusive when no comparative p-value is posted.
    """
    resolved_cancer_type, required, excluded = _resolve_trial_search_inputs(
        result,
        cancer_type=cancer_type,
        required_biomarkers=required_biomarkers,
        excluded_biomarkers=excluded_biomarkers,
    )

    search = _search_clinical_trials(
        cancer_type=resolved_cancer_type,
        required=required,
        excluded=excluded,
        statuses=FINISHED_STATUSES,
        max_pages=max_pages,
        max_results=max_results,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
        include_outcome_classification=True,
    )
    matched_trials = search["matched_trials"]
    outcome_summary = _summarize_finished_trial_outcomes(matched_trials)

    return {
        "data_source": "clinicaltrials.gov_api_v2",
        "search_url": CLINICALTRIALS_API_BASE,
        "cancer_type": resolved_cancer_type,
        "finished_statuses": list(FINISHED_STATUSES),
        "required_biomarkers": required,
        "excluded_biomarkers": excluded,
        "pages_fetched": search["pages_fetched"],
        "trials_scanned": search["trials_scanned"],
        "matched_trial_count": len(matched_trials),
        "outcome_summary": outcome_summary,
        "matched_trials": matched_trials,
    }


if __name__ == "__main__":
    import json
    from types import SimpleNamespace

    demo = SimpleNamespace(
        cancer_type="metastatic non-small cell lung cancer",
        required_biomarkers=["KRAS G12C"],
        excluded_biomarkers=["EGFR", "ALK"],
    )
    print("Active trials:")
    print(json.dumps(search_active_clinical_trials(demo, max_pages=2, max_results=5), indent=2))
    print("\nCompleted trials:")
    print(json.dumps(search_completed_clinical_trials(demo, max_pages=2, max_results=10), indent=2))
