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
ALL_MATCHED_TRIAL_STATUSES = ACTIVE_STATUSES + FINISHED_STATUSES
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


CONTROL_ARM_TYPES = frozenset(
    {
        "PLACEBO_COMPARATOR",
        "ACTIVE_COMPARATOR",
        "SHAM_COMPARATOR",
        "NO_INTERVENTION",
    }
)
_CONTROL_TITLE_MARKERS = (
    "placebo",
    "control arm",
    " comparator",
    "standard of care",
    "sham",
)
_ENROLLMENT_SCOPE_NOTE = (
    "Treatment and control counts are trial-level totals from ClinicalTrials.gov; "
    "per-site enrollment is not published."
)


def _normalize_arm_title(title: str) -> str:
    return re.sub(r"\s+", " ", str(title or "").strip().lower())


def _is_control_arm_label(label: str, control_labels: set[str]) -> bool:
    normalized = _normalize_arm_title(label)
    if normalized in control_labels:
        return True
    padded = f" {normalized} "
    return any(marker in padded for marker in _CONTROL_TITLE_MARKERS)


def _parse_subject_count(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    try:
        return int(float(text))
    except ValueError:
        return None


def _extract_trial_enrollment(study: dict) -> dict:
    """
    Extract treatment vs control enrollment for a trial.

    ClinicalTrials.gov does not publish per-facility arm counts. When available,
    arm-level STARTED counts come from resultsSection.participantFlowModule;
    otherwise only trial-level enrollmentInfo may be present.
    """
    protocol = study.get("protocolSection") or {}
    design = protocol.get("designModule") or {}
    enrollment_info = design.get("enrollmentInfo") or {}
    total_enrolled = _parse_subject_count(enrollment_info.get("count"))
    enrollment_type = enrollment_info.get("type")

    arms = (protocol.get("armsInterventionsModule") or {}).get("armGroups") or []
    control_labels = {
        _normalize_arm_title(arm.get("label"))
        for arm in arms
        if arm.get("type") in CONTROL_ARM_TYPES and arm.get("label")
    }

    patients_enrolled: int | None = None
    control_enrolled: int | None = None
    enrollment_source: str | None = None

    participant_flow = (study.get("resultsSection") or {}).get("participantFlowModule") or {}
    groups = participant_flow.get("groups") or []
    group_id_to_label = {
        group.get("id"): group.get("title")
        for group in groups
        if group.get("id")
    }

    started_counts: dict[str, int] = {}
    for period in participant_flow.get("periods") or []:
        for milestone in period.get("milestones") or []:
            if str(milestone.get("type") or "").upper() != "STARTED":
                continue
            for achievement in milestone.get("achievements") or []:
                group_id = achievement.get("groupId")
                count = _parse_subject_count(achievement.get("numSubjects"))
                if group_id and count is not None:
                    started_counts[group_id] = count

    if started_counts:
        treatment_total = 0
        control_total = 0
        treatment_found = False
        control_found = False
        for group_id, count in started_counts.items():
            label = group_id_to_label.get(group_id) or ""
            if _is_control_arm_label(label, control_labels):
                control_total += count
                control_found = True
            else:
                treatment_total += count
                treatment_found = True
        patients_enrolled = treatment_total if treatment_found else None
        control_enrolled = control_total if control_found else None
        enrollment_source = "participant_flow"
    elif total_enrolled is not None and len(arms) <= 1:
        patients_enrolled = total_enrolled
        control_enrolled = 0
        enrollment_source = "design_module"
    elif total_enrolled is not None:
        enrollment_source = "design_module_total_only"

    if enrollment_source:
        enrollment_note = _ENROLLMENT_SCOPE_NOTE
    else:
        enrollment_note = "Enrollment counts not available for this trial."

    return {
        "patients_enrolled": patients_enrolled,
        "control_enrolled": control_enrolled,
        "total_enrolled": total_enrolled,
        "enrollment_type": enrollment_type,
        "enrollment_source": enrollment_source,
        "enrollment_scope": "trial",
        "enrollment_note": enrollment_note,
    }


def _parse_study_locations(study: dict, trial: dict) -> List[dict]:
    protocol = study.get("protocolSection") or {}
    locations_module = protocol.get("contactsLocationsModule") or {}
    locations = locations_module.get("locations") or []
    sites: List[dict] = []
    enrollment = _extract_trial_enrollment(study)

    for location in locations:
        site_name = str(location.get("facility") or "").strip() or None
        city = str(location.get("city") or "").strip() or None
        state = str(location.get("state") or "").strip() or None
        country = str(location.get("country") or "").strip() or None
        if not any([site_name, city, state, country]):
            continue

        geo_point = location.get("geoPoint") or {}
        latitude = geo_point.get("lat")
        longitude = geo_point.get("lon")

        sites.append(
            {
                "site_name": site_name,
                "city": city,
                "state": state,
                "country": country,
                "latitude": float(latitude) if latitude is not None else None,
                "longitude": float(longitude) if longitude is not None else None,
                "site_status": location.get("status"),
                "trial_status": trial.get("status"),
                "nct_id": trial.get("nct_id"),
                "trial_title": trial.get("title"),
                "trial_url": trial.get("url"),
                **enrollment,
            }
        )

    return sites


def _site_dedupe_key(site: dict) -> tuple[str, str, str, str]:
    return (
        str(site.get("site_name") or "").strip().lower(),
        str(site.get("city") or "").strip().lower(),
        str(site.get("state") or "").strip().lower(),
        str(site.get("country") or "").strip().lower(),
    )


def _trial_site_summary(site: dict) -> dict:
    return {
        "nct_id": site.get("nct_id"),
        "trial_title": site.get("trial_title"),
        "trial_status": site.get("trial_status"),
        "site_status": site.get("site_status"),
        "trial_url": site.get("trial_url"),
        "patients_enrolled": site.get("patients_enrolled"),
        "control_enrolled": site.get("control_enrolled"),
        "total_enrolled": site.get("total_enrolled"),
        "enrollment_type": site.get("enrollment_type"),
        "enrollment_source": site.get("enrollment_source"),
        "enrollment_scope": site.get("enrollment_scope"),
        "enrollment_note": site.get("enrollment_note"),
    }


def _site_row_enrollment(site: dict) -> int:
    total_enrolled = site.get("total_enrolled")
    if isinstance(total_enrolled, (int, float)):
        return int(total_enrolled)

    patients = site.get("patients_enrolled")
    control = site.get("control_enrolled")
    patient_count = int(patients) if isinstance(patients, (int, float)) else 0
    control_count = int(control) if isinstance(control, (int, float)) else 0
    if patient_count or control_count:
        return patient_count + control_count
    return 0


def _allocate_enrollment_by_country(trial_sites: Iterable[dict]) -> dict:
    """
    Allocate each trial's total enrollment across countries proportional to site count.

    CT.gov publishes trial-level enrollment only; this avoids double-counting full trial
    totals in every country where the study listed sites.
    """
    trials: dict[str, dict] = {}
    for site in trial_sites:
        country = str(site.get("country") or "").strip()
        nct_id = site.get("nct_id")
        if not country or not nct_id:
            continue

        trial = trials.setdefault(
            nct_id,
            {"countries": {}, "enrollment": _site_row_enrollment(site)},
        )
        trial["countries"][country] = trial["countries"].get(country, 0) + 1
        enrollment = _site_row_enrollment(site)
        if enrollment > trial["enrollment"]:
            trial["enrollment"] = enrollment

    country_totals: dict[str, dict] = {}
    trials_with_enrollment = 0

    for nct_id, trial in trials.items():
        enrollment = trial["enrollment"]
        if enrollment <= 0:
            continue

        country_sites = trial["countries"]
        total_sites = sum(country_sites.values())
        if total_sites <= 0:
            continue

        trials_with_enrollment += 1
        for country, site_count in country_sites.items():
            allocated = int(round(enrollment * site_count / total_sites))
            row = country_totals.setdefault(
                country,
                {
                    "total_enrollment": 0,
                    "site_count": 0,
                    "nct_ids": set(),
                },
            )
            row["total_enrollment"] += allocated
            row["site_count"] += site_count
            row["nct_ids"].add(nct_id)

    return {
        "country_totals": country_totals,
        "trials_with_enrollment": trials_with_enrollment,
        "matched_trial_count": len(trials),
    }


def summarize_enrollment_by_country(
    *,
    active_sites: Iterable[dict] | None = None,
    completed_sites: Iterable[dict] | None = None,
) -> dict:
    active = _allocate_enrollment_by_country(active_sites or [])
    completed = _allocate_enrollment_by_country(completed_sites or [])

    all_countries = set(active["country_totals"]) | set(completed["country_totals"])
    countries = []
    for country in all_countries:
        active_row = active["country_totals"].get(country, {})
        completed_row = completed["country_totals"].get(country, {})
        active_enrollment = active_row.get("total_enrollment", 0)
        completed_enrollment = completed_row.get("total_enrollment", 0)
        countries.append(
            {
                "country": country,
                "active_enrollment": active_enrollment,
                "completed_enrollment": completed_enrollment,
                "total_enrollment": active_enrollment + completed_enrollment,
                "active_trial_count": len(active_row.get("nct_ids") or set()),
                "completed_trial_count": len(completed_row.get("nct_ids") or set()),
                "trial_count": len(active_row.get("nct_ids") or set())
                + len(completed_row.get("nct_ids") or set()),
                "site_count": (active_row.get("site_count") or 0)
                + (completed_row.get("site_count") or 0),
            }
        )

    countries.sort(
        key=lambda item: (-item["total_enrollment"], item["country"].lower())
    )

    active_total = sum(item["active_enrollment"] for item in countries)
    completed_total = sum(item["completed_enrollment"] for item in countries)

    return {
        "countries": countries,
        "total_enrollment": active_total + completed_total,
        "active_enrollment": active_total,
        "completed_enrollment": completed_total,
        "trials_with_enrollment": active["trials_with_enrollment"]
        + completed["trials_with_enrollment"],
        "active_trials_with_enrollment": active["trials_with_enrollment"],
        "completed_trials_with_enrollment": completed["trials_with_enrollment"],
        "matched_trial_count": active["matched_trial_count"]
        + completed["matched_trial_count"],
        "active_matched_trial_count": active["matched_trial_count"],
        "completed_matched_trial_count": completed["matched_trial_count"],
        "enrollment_note": _ENROLLMENT_SCOPE_NOTE,
    }


def _summarize_enrollment_by_country(trial_sites: Iterable[dict]) -> dict:
    return summarize_enrollment_by_country(completed_sites=trial_sites)


def _dedupe_trials_by_nct(trials: Iterable[dict]) -> List[dict]:
    seen: set[str] = set()
    deduped: List[dict] = []
    for trial in trials:
        nct_id = trial.get("nct_id")
        if not nct_id or nct_id in seen:
            continue
        seen.add(nct_id)
        deduped.append(trial)
    return deduped


def _summarize_unique_site(record: dict) -> dict:
    trials = _dedupe_trials_by_nct(record.get("trials") or [])
    record["trials"] = trials
    record["trial_count"] = len(trials)

    active_trial_count = 0
    completed_trial_count = 0
    total_patients_enrolled = 0
    total_control_enrolled = 0
    patients_found = False
    control_found = False

    for trial in trials:
        status = str(trial.get("trial_status") or "").upper()
        if status in ACTIVE_STATUSES:
            active_trial_count += 1
        else:
            completed_trial_count += 1

        patients = trial.get("patients_enrolled")
        control = trial.get("control_enrolled")
        if isinstance(patients, (int, float)):
            total_patients_enrolled += int(patients)
            patients_found = True
        if isinstance(control, (int, float)):
            total_control_enrolled += int(control)
            control_found = True

    record["active_trial_count"] = active_trial_count
    record["completed_trial_count"] = completed_trial_count
    record["total_patients_enrolled"] = (
        total_patients_enrolled if patients_found else None
    )
    record["total_control_enrolled"] = (
        total_control_enrolled if control_found else None
    )
    return record


def _aggregate_unique_sites(site_rows: Iterable[dict]) -> List[dict]:
    grouped: dict[tuple[str, str, str, str], dict] = {}

    for site in site_rows:
        key = _site_dedupe_key(site)
        if not any(key):
            continue

        existing = grouped.get(key)
        if existing is None:
            grouped[key] = {
                "site_name": site.get("site_name"),
                "city": site.get("city"),
                "state": site.get("state"),
                "country": site.get("country"),
                "latitude": site.get("latitude"),
                "longitude": site.get("longitude"),
                "trial_count": 1,
                "trials": [_trial_site_summary(site)],
            }
            continue

        existing["trial_count"] += 1
        existing["trials"].append(_trial_site_summary(site))
        if existing.get("latitude") is None and site.get("latitude") is not None:
            existing["latitude"] = site.get("latitude")
            existing["longitude"] = site.get("longitude")

    unique_sites = [_summarize_unique_site(record) for record in grouped.values()]
    unique_sites.sort(
        key=lambda item: (
            -(item.get("trial_count") or 0),
            str(item.get("country") or ""),
            str(item.get("state") or ""),
            str(item.get("city") or ""),
            str(item.get("site_name") or ""),
        )
    )
    return unique_sites


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
    include_locations: bool = False,
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
    trial_sites: List[dict] = []
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
            if include_locations:
                sites = _parse_study_locations(study, trial)
                record["sites"] = sites
                trial_sites.extend(sites)

            matched_trials.append(record)
            if len(matched_trials) >= max_results:
                break

        next_page_token = payload.get("nextPageToken")
        if not next_page_token:
            break

    result = {
        "pages_fetched": pages_fetched,
        "trials_scanned": scanned_trials,
        "matched_trials": matched_trials,
    }
    if include_locations:
        result["trial_sites"] = trial_sites
    return result


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
        include_locations=True,
    )
    matched_trials = search["matched_trials"]
    trial_sites = search.get("trial_sites") or []
    for trial in matched_trials:
        trial.pop("sites", None)

    return {
        "data_source": "clinicaltrials.gov_api_v2",
        "search_url": CLINICALTRIALS_API_BASE,
        "cancer_type": cancer_type,
        "active_statuses": list(ACTIVE_STATUSES),
        "required_biomarkers": required,
        "excluded_biomarkers": excluded,
        "pages_fetched": search["pages_fetched"],
        "trials_scanned": search["trials_scanned"],
        "matched_trial_count": len(matched_trials),
        "matched_trials": matched_trials,
        "trial_sites": trial_sites,
    }


def search_completed_clinical_trials(
    result=None,
    *,
    cancer_type: str | None = None,
    required_biomarkers: Iterable[str] | None = None,
    excluded_biomarkers: Iterable[str] | None = None,
    active_trial_sites: Iterable[dict] | None = None,
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
        include_locations=True,
    )
    matched_trials = search["matched_trials"]
    trial_sites = search.get("trial_sites") or []
    outcome_summary = _summarize_finished_trial_outcomes(matched_trials)
    enrollment_by_country = summarize_enrollment_by_country(
        active_sites=active_trial_sites,
        completed_sites=trial_sites,
    )
    for trial in matched_trials:
        trial.pop("sites", None)

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
        "enrollment_by_country": enrollment_by_country,
        "matched_trials": matched_trials,
        "trial_sites": trial_sites,
    }


def search_trial_sites(
    result,
    max_pages: int = 5,
    max_results: int = 50,
    page_size: int = 100,
    timeout_seconds: int = 30,
) -> dict:
    """
    Search ClinicalTrials.gov for trials matching eligibility and extract site locations.

    Returns site name, city, state, country, site-level status, parent trial status,
    and trial-level treatment/control enrollment counts for every listed location.
    Per-site arm enrollment is not published on ClinicalTrials.gov; counts are repeated
    on each site row with enrollment_scope="trial".
    """
    required = result.required_biomarkers or []
    excluded = result.excluded_biomarkers or []
    cancer_type = _condition_query(getattr(result, "cancer_type", None))

    search = _search_clinical_trials(
        cancer_type=cancer_type,
        required=required,
        excluded=excluded,
        statuses=ALL_MATCHED_TRIAL_STATUSES,
        max_pages=max_pages,
        max_results=max_results,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
        include_outcome_classification=False,
        include_locations=True,
    )

    trial_sites = search.get("trial_sites") or []
    unique_sites = _aggregate_unique_sites(trial_sites)
    matched_trials = search["matched_trials"]
    for trial in matched_trials:
        trial.pop("sites", None)

    return {
        "data_source": "clinicaltrials.gov_api_v2",
        "search_url": CLINICALTRIALS_API_BASE,
        "cancer_type": cancer_type,
        "trial_statuses": list(ALL_MATCHED_TRIAL_STATUSES),
        "required_biomarkers": required,
        "excluded_biomarkers": excluded,
        "pages_fetched": search["pages_fetched"],
        "trials_scanned": search["trials_scanned"],
        "matched_trial_count": len(matched_trials),
        "trial_site_count": len(trial_sites),
        "unique_site_count": len(unique_sites),
        "matched_trials": matched_trials,
        "trial_sites": trial_sites,
        "unique_sites": unique_sites,
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
    print("\nTrial sites:")
    print(json.dumps(search_trial_sites(demo, max_pages=2, max_results=5), indent=2))
