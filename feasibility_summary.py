from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

RATING_VALUES = ("Strong", "Moderate", "Challenging", "Weak")
DIMENSION_KEYS = (
    "biomarker_rationale",
    "protocol_clarity",
    "enrollment_speed",
    "patient_demographic_fit",
    "rwd_treatment_data_feasibility",
    "overall_survival_data",
)


class FeasibilityDimension(BaseModel):
    dimension: str
    rating: str
    why: str


class RecommendedEndpoints(BaseModel):
    recommended_phase: str
    primary_endpoint: str
    primary_rationale: str
    secondary_endpoints: list[str] = Field(default_factory=list)
    secondary_rationale: str | None = None


class FeasibilitySummaryResult(BaseModel):
    overall_verdict: str
    dimensions: list[FeasibilityDimension]
    recommended_endpoints: RecommendedEndpoints
    suggestions_to_improve_feasibility: list[str] = Field(default_factory=list)


FEASIBILITY_SUMMARY_TOOL_NAME = "submit_feasibility_summary"

FEASIBILITY_DIMENSION_LABELS = (
    "Biomarker rationale",
    "Protocol clarity",
    "Enrollment speed",
    "Patient demographic fit",
    "RWD treatment data feasibility",
    "Overall survival data",
)

FEASIBILITY_SUMMARY_TOOL = {
    "type": "function",
    "function": {
        "name": FEASIBILITY_SUMMARY_TOOL_NAME,
        "description": (
            "Submit the structured oncology clinical trial feasibility assessment "
            "derived from GeneTrail search results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "overall_verdict": {
                    "type": "string",
                    "description": "1-2 sentence feasibility conclusion.",
                },
                "dimensions": {
                    "type": "array",
                    "description": (
                        "Six feasibility dimension ratings with data-backed rationale."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "dimension": {
                                "type": "string",
                                "enum": list(FEASIBILITY_DIMENSION_LABELS),
                            },
                            "rating": {
                                "type": "string",
                                "enum": list(RATING_VALUES),
                            },
                            "why": {
                                "type": "string",
                                "description": (
                                    "Rationale citing specific numbers from the search context."
                                ),
                            },
                        },
                        "required": ["dimension", "rating", "why"],
                    },
                    "minItems": 6,
                    "maxItems": 6,
                },
                "recommended_endpoints": {
                    "type": "object",
                    "properties": {
                        "recommended_phase": {
                            "type": "string",
                            "description": "e.g. Phase 2 single-arm or Phase 3 randomized",
                        },
                        "primary_endpoint": {"type": "string"},
                        "primary_rationale": {"type": "string"},
                        "secondary_endpoints": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "secondary_rationale": {"type": "string"},
                    },
                    "required": [
                        "recommended_phase",
                        "primary_endpoint",
                        "primary_rationale",
                        "secondary_endpoints",
                        "secondary_rationale",
                    ],
                },
                "suggestions_to_improve_feasibility": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "overall_verdict",
                "dimensions",
                "recommended_endpoints",
                "suggestions_to_improve_feasibility",
            ],
        },
    },
}


def _parse_tool_call_arguments(response: Any) -> dict[str, Any]:
    message = response.choices[0].message
    tool_calls = message.tool_calls or []
    if not tool_calls:
        fallback = (message.content or "").strip()
        if fallback:
            return _parse_json_response(fallback)
        raise ValueError("Model did not return a feasibility summary tool call.")

    tool_call = tool_calls[0]
    if tool_call.function.name != FEASIBILITY_SUMMARY_TOOL_NAME:
        raise ValueError(
            f"Unexpected tool call: {tool_call.function.name!r}. "
            f"Expected {FEASIBILITY_SUMMARY_TOOL_NAME!r}."
        )

    try:
        data = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in tool arguments:\n{tool_call.function.arguments}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError("Feasibility summary tool arguments must be a JSON object.")
    return data


def _parse_json_response(raw_text: str) -> dict:
    text = raw_text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start == -1:
            raise ValueError(f"No JSON object found in model output:\n{text}") from None
        data, _ = json.JSONDecoder().raw_decode(text[start:])
        return data


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def _normalize_rating(value: str) -> str:
    cleaned = str(value or "").strip().capitalize()
    for rating in RATING_VALUES:
        if cleaned.lower() == rating.lower():
            return rating
    return "Moderate"


def _top_distribution_items(
    distribution: list[dict[str, Any]] | None,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return [
        {
            "value": item.get("value") or item.get("label"),
            "count": item.get("count") or item.get("patient_count"),
            "percentage": item.get("percentage"),
        }
        for item in (distribution or [])[:limit]
    ]


def _compact_attribute_by_os_status(
    rows: list[dict[str, Any]] | None,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in (rows or [])[:limit]:
        compact.append(
            {
                "category": row.get("value") or row.get("label"),
                "patient_count": row.get("patient_count"),
                "os_status_distribution": row.get("os_status_distribution") or [],
            }
        )
    return compact


def _summarize_patient_metadata(
    patient_metadata_stats: dict[str, Any],
) -> dict[str, Any]:
    if not patient_metadata_stats:
        return {}

    coverage = patient_metadata_stats.get("metadata_coverage") or {}
    sparse_fields = [
        field
        for field, stats in coverage.items()
        if isinstance(stats, dict) and stats.get("percentage", 100) < 25
    ]

    return {
        "eligible_patient_count": patient_metadata_stats.get("eligible_patient_count"),
        "patients_with_metadata": patient_metadata_stats.get("patients_with_metadata"),
        "patients_with_os_status": patient_metadata_stats.get("patients_with_os_status"),
        "metadata_coverage": coverage,
        "sparse_metadata_fields": sparse_fields,
        "age_summary": patient_metadata_stats.get("age_summary") or {},
        "sex_distribution": _top_distribution_items(
            patient_metadata_stats.get("sex_distribution")
        ),
        "race_distribution": _top_distribution_items(
            patient_metadata_stats.get("race_distribution")
        ),
        "ethnicity_distribution": _top_distribution_items(
            patient_metadata_stats.get("ethnicity_distribution")
        ),
        "smoking_status_distribution": _top_distribution_items(
            patient_metadata_stats.get("smoking_status_distribution")
        ),
        "stage_distribution": _top_distribution_items(
            patient_metadata_stats.get("stage_distribution")
        ),
        "ecog_status_distribution": _top_distribution_items(
            patient_metadata_stats.get("ecog_status_distribution")
        ),
        "sex_by_os_status": _compact_attribute_by_os_status(
            patient_metadata_stats.get("sex_by_os_status")
        ),
        "race_by_os_status": _compact_attribute_by_os_status(
            patient_metadata_stats.get("race_by_os_status")
        ),
        "smoking_status_by_os_status": _compact_attribute_by_os_status(
            patient_metadata_stats.get("smoking_status_by_os_status")
        ),
        "stage_by_os_status": _compact_attribute_by_os_status(
            patient_metadata_stats.get("stage_by_os_status")
        ),
        "ecog_status_by_os_status": _compact_attribute_by_os_status(
            patient_metadata_stats.get("ecog_status_by_os_status")
        ),
        "age_by_os_status": _compact_attribute_by_os_status(
            patient_metadata_stats.get("age_by_os_status")
        ),
    }


def _summarize_depmap(depmap: dict[str, Any]) -> dict[str, Any]:
    if not depmap:
        return {}

    gene_effects = depmap.get("gene_effect_summary") or []
    strong_dependency = [
        gene
        for gene in gene_effects
        if gene.get("mean_gene_effect") is not None and gene["mean_gene_effect"] <= -0.5
    ]

    drug_summary = depmap.get("drug_sensitivity_summary") or []
    top_sensitive = [
        {
            "drug_name": row.get("drug_name"),
            "screen_type": row.get("screen_type"),
            "mean_log_fold_change": row.get("mean_log_fold_change"),
            "best_log_fold_change": row.get("best_log_fold_change"),
            "sensitive_model_count": row.get("sensitive_model_count"),
            "moa": row.get("moa"),
            "target": row.get("target"),
            "phase": row.get("phase"),
        }
        for row in drug_summary[:8]
    ]

    return {
        "eligible_cell_lines": depmap.get("eligible_cell_lines"),
        "total_cell_lines": depmap.get("total_cell_lines"),
        "cell_lines_with_required_biomarkers": depmap.get(
            "cell_lines_with_required_biomarkers"
        ),
        "strong_gene_dependency_count": len(strong_dependency),
        "strong_gene_dependencies": [
            {
                "gene_symbol": gene.get("gene_symbol"),
                "mean_gene_effect": gene.get("mean_gene_effect"),
                "model_count": gene.get("model_count"),
            }
            for gene in strong_dependency[:5]
        ],
        "gene_effect_summary": gene_effects[:6],
        "prism_drug_count": len(drug_summary),
        "top_prism_sensitive_drugs": top_sensitive,
    }


def _summarize_trial_sites(trial_sites: dict[str, Any]) -> dict[str, Any]:
    if not trial_sites:
        return {}

    unique_sites = trial_sites.get("unique_sites") or []
    country_counts: dict[str, int] = {}
    sites_with_coordinates = 0

    for site in unique_sites:
        country = str(site.get("country") or "").strip()
        if country:
            country_counts[country] = country_counts.get(country, 0) + 1
        if site.get("latitude") is not None and site.get("longitude") is not None:
            sites_with_coordinates += 1

    top_countries = sorted(country_counts.items(), key=lambda item: (-item[1], item[0]))[:10]

    return {
        "matched_trial_count": trial_sites.get("matched_trial_count"),
        "trial_site_count": trial_sites.get("trial_site_count"),
        "unique_site_count": trial_sites.get("unique_site_count"),
        "unique_country_count": len(country_counts),
        "sites_with_coordinates": sites_with_coordinates,
        "top_countries_by_unique_site_count": [
            {"country": country, "unique_site_count": count}
            for country, count in top_countries
        ],
        "sample_unique_sites": [
            {
                "site_name": site.get("site_name"),
                "city": site.get("city"),
                "state": site.get("state"),
                "country": site.get("country"),
                "trial_count": site.get("trial_count"),
            }
            for site in unique_sites[:5]
        ],
    }


def _summarize_enrollment_by_country(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary:
        return {}

    countries = summary.get("countries") or []

    return {
        "total_enrollment": summary.get("total_enrollment"),
        "active_enrollment": summary.get("active_enrollment"),
        "completed_enrollment": summary.get("completed_enrollment"),
        "trials_with_enrollment": summary.get("trials_with_enrollment"),
        "active_trials_with_enrollment": summary.get("active_trials_with_enrollment"),
        "completed_trials_with_enrollment": summary.get(
            "completed_trials_with_enrollment"
        ),
        "active_matched_trial_count": summary.get("active_matched_trial_count"),
        "completed_matched_trial_count": summary.get("completed_matched_trial_count"),
        "country_count": len(countries),
        "top_countries": [
            {
                "country": row.get("country"),
                "total_enrollment": row.get("total_enrollment"),
                "active_enrollment": row.get("active_enrollment"),
                "completed_enrollment": row.get("completed_enrollment"),
                "trial_count": row.get("trial_count"),
            }
            for row in countries[:10]
        ],
        "enrollment_note": summary.get("enrollment_note"),
    }


def _summarize_drugs(existing_drugs: dict[str, Any]) -> dict[str, Any]:
    sensitive = 0
    resistant = 0
    samples: list[dict[str, Any]] = []

    for drug in existing_drugs.get("matched_drugs") or []:
        response_type = str(drug.get("response_type") or "").lower()
        if "sensitive" in response_type or "responsive" in response_type:
            sensitive += 1
        elif "resistant" in response_type:
            resistant += 1

        if len(samples) < 5:
            samples.append(
                {
                    "drug_names": drug.get("drug_names"),
                    "response_type": drug.get("response_type"),
                    "evidence_label": drug.get("evidence_label"),
                }
            )

    return {
        "matched_drug_count": existing_drugs.get("matched_drug_count", 0),
        "sensitive_association_count": sensitive,
        "resistant_association_count": resistant,
        "sample_drugs": samples,
    }


def _build_search_context(
    eligibility: Any,
    stats: dict[str, Any],
    treatment_stats: dict[str, Any],
    control_stats: dict[str, Any],
    clinical_trials: dict[str, Any],
    completed_clinical_trials: dict[str, Any],
    existing_drugs: dict[str, Any],
    patient_metadata_stats: dict[str, Any] | None = None,
    depmap: dict[str, Any] | None = None,
    trial_sites: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eligibility_data = _as_dict(eligibility)
    trial_samples = [
        {
            "nct_id": trial.get("nct_id"),
            "status": trial.get("status"),
            "title": trial.get("title"),
        }
        for trial in (clinical_trials.get("matched_trials") or [])[:8]
    ]
    completed_trial_samples = [
        {
            "nct_id": trial.get("nct_id"),
            "status": trial.get("status"),
            "title": trial.get("title"),
            "outcome_category": trial.get("outcome_category"),
            "outcome_label": trial.get("outcome_label"),
            "outcome_reason": trial.get("outcome_reason"),
            "has_results": trial.get("has_results"),
        }
        for trial in (completed_clinical_trials.get("matched_trials") or [])[:8]
    ]

    return {
        "eligibility": eligibility_data,
        "patient_stats": {
            "unique_patients_with_cancer_type": stats.get("unique_patients_with_cancer_type"),
            "patients_with_required_biomarkers": stats.get("patients_with_required_biomarkers"),
            "eligible_patients": stats.get("eligible_patients"),
            "studies_searched": stats.get("studies_searched"),
            "gene_patient_counts": stats.get("gene_patient_counts"),
        },
        "treatment_stats": {
            "biomarker_eligible_count": treatment_stats.get("biomarker_eligible_count"),
            "prior_treatment_matched_count": treatment_stats.get("prior_treatment_matched_count"),
            "patients_with_os_status": treatment_stats.get("patients_with_os_status"),
            "patients_with_os_days": treatment_stats.get("patients_with_os_days"),
            "os_status_distribution": treatment_stats.get("os_status_distribution"),
            "os_days_distribution": treatment_stats.get("os_days_distribution"),
        },
        "control_stats": control_stats,
        "clinical_trials": {
            "matched_trial_count": clinical_trials.get("matched_trial_count"),
            "trials_scanned": clinical_trials.get("trials_scanned"),
            "sample_matched_trials": trial_samples,
        },
        "completed_clinical_trials": {
            "matched_trial_count": completed_clinical_trials.get("matched_trial_count"),
            "trials_scanned": completed_clinical_trials.get("trials_scanned"),
            "outcome_summary": completed_clinical_trials.get("outcome_summary") or {},
            "sample_matched_trials": completed_trial_samples,
        },
        "enrollment_by_country": _summarize_enrollment_by_country(
            completed_clinical_trials.get("enrollment_by_country") or {}
        ),
        "trial_sites": _summarize_trial_sites(trial_sites or {}),
        "depmap": _summarize_depmap(depmap or {}),
        "existing_drugs": _summarize_drugs(existing_drugs),
        "patient_metadata": _summarize_patient_metadata(patient_metadata_stats or {}),
    }


def _normalize_summary_data(data: dict[str, Any]) -> dict[str, Any]:
    dimensions_raw = data.get("dimensions") or []
    dimensions: list[dict[str, str]] = []

    if isinstance(dimensions_raw, dict):
        for key in DIMENSION_KEYS:
            item = dimensions_raw.get(key) or {}
            dimensions.append(
                {
                    "dimension": key.replace("_", " ").title(),
                    "rating": _normalize_rating(item.get("rating", "Moderate")),
                    "why": str(item.get("why") or item.get("rationale") or "").strip(),
                }
            )
    elif isinstance(dimensions_raw, list):
        for item in dimensions_raw:
            if not isinstance(item, dict):
                continue
            dimension = str(item.get("dimension") or item.get("name") or "Dimension").strip()
            dimensions.append(
                {
                    "dimension": dimension,
                    "rating": _normalize_rating(item.get("rating", "Moderate")),
                    "why": str(item.get("why") or item.get("rationale") or "").strip(),
                }
            )

    endpoints_raw = data.get("recommended_endpoints") or {}
    if not isinstance(endpoints_raw, dict):
        endpoints_raw = {}

    secondary = endpoints_raw.get("secondary_endpoints") or data.get("secondary_endpoints") or []
    if isinstance(secondary, str):
        secondary = [part.strip() for part in secondary.split(",") if part.strip()]

    suggestions = data.get("suggestions_to_improve_feasibility") or data.get("suggestions") or []
    if isinstance(suggestions, str):
        suggestions = [suggestions]

    return {
        "overall_verdict": str(data.get("overall_verdict") or data.get("summary") or "").strip(),
        "dimensions": dimensions,
        "recommended_endpoints": {
            "recommended_phase": str(
                endpoints_raw.get("recommended_phase") or data.get("recommended_phase") or ""
            ).strip(),
            "primary_endpoint": str(
                endpoints_raw.get("primary_endpoint") or data.get("primary_endpoint") or ""
            ).strip(),
            "primary_rationale": str(
                endpoints_raw.get("primary_rationale") or data.get("primary_rationale") or ""
            ).strip(),
            "secondary_endpoints": [str(item).strip() for item in secondary if str(item).strip()],
            "secondary_rationale": str(
                endpoints_raw.get("secondary_rationale") or data.get("secondary_rationale") or ""
            ).strip()
            or None,
        },
        "suggestions_to_improve_feasibility": [
            str(item).strip() for item in suggestions if str(item).strip()
        ],
    }


def feasibility_summary(
    eligibility: Any,
    stats: dict[str, Any],
    treatment_stats: dict[str, Any],
    control_stats: dict[str, Any],
    clinical_trials: dict[str, Any],
    completed_clinical_trials: dict[str, Any],
    existing_drugs: dict[str, Any],
    patient_metadata_stats: dict[str, Any] | None = None,
    depmap: dict[str, Any] | None = None,
    trial_sites: dict[str, Any] | None = None,
    *,
    model: str = OPENAI_MODEL,
) -> dict[str, Any]:
    """
    Generate a structured clinical trial feasibility summary from GeneTrail search results.

    Uses OpenAI to interpret patient stats, treatment/survival data, patient metadata,
    active and completed competing trials, trial site geography, enrollment by country,
    DepMap PRISM drug sensitivity, and existing drug evidence into ratings, endpoint
    recommendations, and suggestions.
    """
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to genetrail_ai/.env")

    context = _build_search_context(
        eligibility,
        stats,
        treatment_stats,
        control_stats,
        clinical_trials,
        completed_clinical_trials,
        existing_drugs,
        patient_metadata_stats,
        depmap,
        trial_sites,
    )

    prompt = f"""
You are an oncology clinical trial feasibility analyst reviewing GeneTrail search results.

Use ONLY the JSON context below. Cite specific numbers from the data in each rationale.
Be concise but specific. Do not invent patient counts or trial counts.
Call the {FEASIBILITY_SUMMARY_TOOL_NAME} tool with your complete assessment.

Guidance:
- Biomarker rationale: use patient_stats eligible pool, depmap.eligible_cell_lines, depmap.strong_gene_dependencies, and depmap.top_prism_sensitive_drugs when present. Note whether biomarker-matched DepMap models show dependency and whether PRISM supports preclinical activity for relevant MOAs/targets.
- Enrollment speed: consider active clinical_trials.matched_trial_count, completed_clinical_trials.matched_trial_count, biomarker pool size, trial_sites.unique_site_count, trial_sites.unique_country_count, trial_sites.top_countries_by_unique_site_count, and enrollment_by_country.top_countries (active vs completed allocated enrollment). Dense site maps and high country enrollment suggest established recruitment infrastructure but also competition.
- Patient demographic fit: compare eligibility fields (age, ECOG, stage, smoking, line of therapy) against patient_metadata coverage, distributions, and *_by_os_status breakdowns. Flag sparse metadata fields, cohorts that may not match inclusion criteria, underrepresented subgroups, and whether OS signal differs by age/sex/race/stage/ECOG.
- Completed trials: use completed_clinical_trials.outcome_summary (positive, negative, inconclusive, no-results, stopped/failed counts) and sample_matched_trials to assess precedent, competitive saturation, and endpoint feasibility.
- RWD treatment data feasibility: compare biomarker_eligible_count vs prior_treatment_matched_count.
- Overall survival data: use patients_with_os_status/os_days and os_status_distribution; note if Unknown is high.
- Existing drugs: distinguish sensitive vs resistant associations when relevant.
- Trial site map: use trial_sites counts, geographic spread, and sample_unique_sites to comment on where competing trials already operate and whether site density aligns with enrollment_by_country leaders.
- DepMap PRISM: when depmap.top_prism_sensitive_drugs is present, cite mean_log_fold_change and sensitive_model_count for the most sensitive compounds; connect to biomarker rationale or differentiation vs existing_drugs when supported.
- Enrollment by country: cite enrollment_by_country.active_enrollment vs completed_enrollment and top_countries when rating enrollment speed or suggesting site strategy.
- Suggestions must include at least 1-2 metadata-informed recommendations when patient_metadata is present (e.g., broaden ECOG if cohort ECOG is sparse, age caps, site diversity for race/ethnicity gaps, smoking documentation, stage-specific enrollment strategy, subgroup monitoring).
- Suggest ctDNA screening, central lab confirmation, differentiation vs competing trials, site prioritization in top enrollment countries, and preclinical validation from DepMap PRISM when supported by the context.

GeneTrail search context:
{json.dumps(context, indent=2)}
"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You assess oncology clinical trial feasibility from structured search results. "
                    f"Always call {FEASIBILITY_SUMMARY_TOOL_NAME} with your assessment."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        tools=[FEASIBILITY_SUMMARY_TOOL],
        tool_choice={
            "type": "function",
            "function": {"name": FEASIBILITY_SUMMARY_TOOL_NAME},
        },
        temperature=0,
    )
    print("########################################################")
    print(response.choices[0].message.tool_calls[0].function.arguments)
    print("########################################################")

    normalized = _normalize_summary_data(_parse_tool_call_arguments(response))
    summary = FeasibilitySummaryResult(**normalized)

    return {
        "data_source": "openai_feasibility_summary",
        "model": model,
        "search_context": context,
        **summary.model_dump(),
    }


if __name__ == "__main__":
    from types import SimpleNamespace

    demo_eligibility = SimpleNamespace(
        cancer_type="metastatic non-small cell lung cancer",
        stage="metastatic",
        line_of_therapy=None,
        required_biomarkers=["KRAS G12C mutation"],
        excluded_biomarkers=["EGFR activating mutations", "ALK fusions"],
        prior_treatments=["platinum-based chemotherapy"],
        ecog_status="0 or 1",
        sample_requirements=[],
        assay_requirements=[],
        uncertainty_flags=[],
    )
    demo_stats = {
        "unique_patients_with_cancer_type": 13005,
        "patients_with_required_biomarkers": 1226,
        "eligible_patients": 1226,
        "studies_searched": 37,
    }
    demo_treatment_stats = {
        "biomarker_eligible_count": 1226,
        "prior_treatment_matched_count": 119,
        "patients_with_os_status": 26,
        "patients_with_os_days": 26,
        "os_status_distribution": [
            {"status": "Unknown", "count": 93, "percentage": 78.2},
            {"status": "Living", "count": 16, "percentage": 13.4},
            {"status": "Deceased", "count": 10, "percentage": 8.4},
        ],
    }
    demo_control_stats = {
        "with_treatment": {"patient_count": 4182, "living_percentage": 39.3},
        "without_treatment": {"patient_count": 8823, "living_percentage": 40.1},
    }
    demo_clinical_trials = {"matched_trial_count": 48, "trials_scanned": 350, "matched_trials": []}
    demo_completed_clinical_trials = {
        "matched_trial_count": 6,
        "trials_scanned": 280,
        "outcome_summary": {
            "completed_positive_count": 1,
            "completed_negative_count": 0,
            "completed_inconclusive_count": 2,
            "completed_no_results_count": 1,
            "study_stopped_count": 2,
            "failed_count": 2,
            "completed_with_results_count": 3,
        },
        "matched_trials": [],
    }
    demo_drugs = {"matched_drug_count": 3, "matched_drugs": []}
    demo_depmap = {
        "eligible_cell_lines": 12,
        "total_cell_lines": 180,
        "cell_lines_with_required_biomarkers": 18,
        "gene_effect_summary": [
            {"gene_symbol": "KRAS", "mean_gene_effect": -0.62, "model_count": 10},
        ],
        "drug_sensitivity_summary": [
            {
                "drug_name": "Sotorasib",
                "screen_type": "PRISM",
                "mean_log_fold_change": -1.42,
                "sensitive_model_count": 8,
                "moa": "KRAS G12C inhibitor",
            }
        ],
    }
    demo_trial_sites = {
        "matched_trial_count": 42,
        "trial_site_count": 310,
        "unique_site_count": 145,
        "unique_sites": [
            {
                "site_name": "Memorial Sloan Kettering",
                "city": "New York",
                "state": "New York",
                "country": "United States",
                "trial_count": 6,
            }
        ],
    }
    demo_patient_metadata = {
        "eligible_patient_count": 119,
        "patients_with_metadata": 110,
        "patients_with_os_status": 26,
        "metadata_coverage": {
            "age": {"count": 45, "percentage": 37.8},
            "sex": {"count": 108, "percentage": 90.8},
            "race": {"count": 62, "percentage": 52.1},
            "ecog_status": {"count": 8, "percentage": 6.7},
        },
        "sparse_metadata_fields": ["ecog_status", "stage"],
        "age_summary": {"count": 45, "average": 64.2, "min": 38, "max": 89},
        "sex_distribution": [
            {"value": "Female", "count": 58, "percentage": 53.7},
            {"value": "Male", "count": 50, "percentage": 46.3},
        ],
        "race_distribution": [
            {"value": "White", "count": 40, "percentage": 64.5},
            {"value": "Asian", "count": 12, "percentage": 19.4},
        ],
        "ecog_status_distribution": [
            {"value": "1", "count": 5, "percentage": 62.5},
            {"value": "0", "count": 3, "percentage": 37.5},
        ],
        "sex_by_os_status": [
            {
                "category": "Female",
                "patient_count": 58,
                "os_status_distribution": [
                    {"status": "Unknown", "count": 45, "percentage": 77.6},
                    {"status": "Living", "count": 9, "percentage": 15.5},
                    {"status": "Deceased", "count": 4, "percentage": 6.9},
                ],
            }
        ],
    }

    print(
        json.dumps(
            feasibility_summary(
                demo_eligibility,
                demo_stats,
                demo_treatment_stats,
                demo_control_stats,
                demo_clinical_trials,
                demo_completed_clinical_trials,
                demo_drugs,
                demo_patient_metadata,
                depmap=demo_depmap,
                trial_sites=demo_trial_sites,
            ),
            indent=2,
        )
    )
