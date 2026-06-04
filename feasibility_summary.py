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
        "existing_drugs": _summarize_drugs(existing_drugs),
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
    *,
    model: str = OPENAI_MODEL,
) -> dict[str, Any]:
    """
    Generate a structured clinical trial feasibility summary from GeneTrail search results.

    Uses OpenAI to interpret patient stats, treatment/survival data, active and completed
    competing trials, and existing drug evidence into ratings, endpoint recommendations,
    and suggestions.
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
    )

    prompt = f"""
You are an oncology clinical trial feasibility analyst reviewing GeneTrail search results.

Use ONLY the JSON context below. Cite specific numbers from the data in each rationale.
Be concise but specific. Do not invent patient counts or trial counts.

Return valid JSON with this shape:
{{
  "overall_verdict": "1-2 sentence feasibility conclusion",
  "dimensions": [
    {{
      "dimension": "Biomarker rationale",
      "rating": "Strong|Moderate|Challenging|Weak",
      "why": "..."
    }},
    {{
      "dimension": "Protocol clarity",
      "rating": "Strong|Moderate|Challenging|Weak",
      "why": "..."
    }},
    {{
      "dimension": "Enrollment speed",
      "rating": "Strong|Moderate|Challenging|Weak",
      "why": "..."
    }},
    {{
      "dimension": "RWD treatment data feasibility",
      "rating": "Strong|Moderate|Challenging|Weak",
      "why": "..."
    }},
    {{
      "dimension": "Overall survival data",
      "rating": "Strong|Moderate|Challenging|Weak",
      "why": "..."
    }}
  ],
  "recommended_endpoints": {{
    "recommended_phase": "e.g. Phase 2 single-arm or Phase 3 randomized",
    "primary_endpoint": "...",
    "primary_rationale": "...",
    "secondary_endpoints": ["...", "..."],
    "secondary_rationale": "..."
  }},
  "suggestions_to_improve_feasibility": [
    "...",
    "..."
  ]
}}

Guidance:
- Enrollment speed: consider active clinical_trials.matched_trial_count, completed_clinical_trials.matched_trial_count, and biomarker pool size.
- Completed trials: use completed_clinical_trials.outcome_summary (positive, negative, inconclusive, no-results, stopped/failed counts) and sample_matched_trials to assess precedent, competitive saturation, and endpoint feasibility.
- RWD treatment data feasibility: compare biomarker_eligible_count vs prior_treatment_matched_count.
- Overall survival data: use patients_with_os_status/os_days and os_status_distribution; note if Unknown is high.
- Existing drugs: distinguish sensitive vs resistant associations when relevant.
- Suggest ctDNA screening, central lab confirmation, differentiation vs competing trials when supported by active or completed trial landscape.

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
                    "Respond with JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )

    raw_text = response.choices[0].message.content or ""
    normalized = _normalize_summary_data(_parse_json_response(raw_text))
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
            ),
            indent=2,
        )
    )
