import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from cbioportal_search import search_cbioportal_for_patients, search_neon_for_treatments
from clinicaltrails_search import (
    search_active_clinical_trials,
    search_completed_clinical_trials,
)
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field
from control_stats import load_control_stats
from feasibility_summary import feasibility_summary
from vicc_search import search_vicc_drugs

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class TrialEligibility(BaseModel):
    cancer_type: str | None = Field(default=None)
    stage: str | None = Field(default=None)
    line_of_therapy: str | None = Field(default=None)
    required_biomarkers: list[str] = Field(default_factory=list)
    excluded_biomarkers: list[str] = Field(default_factory=list)
    prior_treatments: list[str] = Field(default_factory=list)
    ecog_status: str | None = Field(default=None)
    sample_requirements: list[str] = Field(default_factory=list)
    assay_requirements: list[str] = Field(default_factory=list)
    uncertainty_flags: list[str] = Field(default_factory=list)


LIST_FIELDS = (
    "required_biomarkers",
    "excluded_biomarkers",
    "prior_treatments",
    "sample_requirements",
    "assay_requirements",
    "uncertainty_flags",
)
STRING_FIELDS = ("cancer_type", "stage", "line_of_therapy", "ecog_status")


def _normalize_eligibility_data(data: dict) -> dict:
    """Coerce common LLM JSON variants into TrialEligibility field types."""
    normalized: dict = {}
    for key, value in data.items():
        if key in LIST_FIELDS:
            if value is None:
                normalized[key] = []
            elif isinstance(value, list):
                normalized[key] = [str(item) for item in value if item is not None]
            else:
                normalized[key] = [str(value)]
        elif key in STRING_FIELDS:
            if value is None:
                normalized[key] = None
            elif isinstance(value, list):
                parts = [str(item) for item in value if item is not None]
                if len(parts) == 2 and all(part.isdigit() for part in parts):
                    normalized[key] = " or ".join(parts)
                else:
                    normalized[key] = ", ".join(parts)
            else:
                normalized[key] = str(value)
        else:
            normalized[key] = value
    return normalized


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


def extract_trial_eligibility(
    protocol_text: str,
    model: str = OPENAI_MODEL,
) -> TrialEligibility:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to genetrail_ai/.env")

    prompt = f"""
You are helping extract structured eligibility criteria from an oncology clinical trial protocol.

Return only valid JSON with these fields:
- cancer_type
- stage
- line_of_therapy
- required_biomarkers
- excluded_biomarkers
- prior_treatments
- ecog_status
- sample_requirements
- assay_requirements
- uncertainty_flags

Protocol text:
{protocol_text}
"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract structured oncology trial eligibility criteria. "
                    "Respond with JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )

    raw_text = response.choices[0].message.content or ""
    data = _normalize_eligibility_data(_parse_json_response(raw_text))
    return TrialEligibility(**data)


def _save_search_result(
    results_dir: Path,
    *,
    prefix: str,
    label: str,
    payload: dict,
    run_timestamp: str,
) -> None:
    payload_json = json.dumps(payload, indent=2)
    print(f"\n{label}:")
    print(payload_json)

    output_path = results_dir / f"{prefix}_{run_timestamp}.json"
    output_path.write_text(payload_json + "\n", encoding="utf-8")
    print(f"Saved {prefix.replace('_', ' ')} to: {output_path}")


def _run_search_analyses(result: TrialEligibility) -> dict[str, dict]:
    with ThreadPoolExecutor(max_workers=5) as executor:
        stats_future = executor.submit(search_cbioportal_for_patients, result)
        treatments_future = executor.submit(search_neon_for_treatments, result)
        clinical_trials_future = executor.submit(search_active_clinical_trials, result)
        completed_trials_future = executor.submit(search_completed_clinical_trials, result)
        drugs_future = executor.submit(
            search_vicc_drugs,
            result.required_biomarkers,
            result.cancer_type,
        )

        return {
            "stats": stats_future.result(),
            "treatments": treatments_future.result(),
            "clinical_trials": clinical_trials_future.result(),
            "completed_clinical_trials": completed_trials_future.result(),
            "drugs": drugs_future.result(),
        }


if __name__ == "__main__":
    protocol = """
    Patients must have metastatic non-small cell lung cancer.
    Eligible patients must have KRAS G12C mutation confirmed by tumor tissue or ctDNA.
    Patients with EGFR activating mutations or ALK fusions are excluded.
    Prior platinum-based chemotherapy is required.
    ECOG performance status must be 0 or 1.
    """

    result = extract_trial_eligibility(protocol)
    output_json = result.model_dump_json(indent=2)
    print(output_json)

    results_dir = Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)
    output_path = results_dir / f"trial_eligibility_{datetime.now():%Y%m%d_%H%M%S}.json"
    output_path.write_text(output_json + "\n", encoding="utf-8")
    print(f"\nSaved results to: {output_path}")

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    search_results = _run_search_analyses(result)

    stats = search_results["stats"]
    _save_search_result(
        results_dir,
        prefix="cbioportal_stats",
        label="NSCLC patient counts from cBioPortal",
        payload=stats,
        run_timestamp=run_timestamp,
    )

    treatments = search_results["treatments"]
    _save_search_result(
        results_dir,
        prefix="treatments",
        label="Treatments from cBioPortal",
        payload=treatments,
        run_timestamp=run_timestamp,
    )

    clinical_trials = search_results["clinical_trials"]
    _save_search_result(
        results_dir,
        prefix="clinical_trials",
        label="Clinical trials from ClinicalTrials.gov",
        payload=clinical_trials,
        run_timestamp=run_timestamp,
    )

    completed_clinical_trials = search_results["completed_clinical_trials"]
    _save_search_result(
        results_dir,
        prefix="completed_clinical_trials",
        label="Completed clinical trials from ClinicalTrials.gov",
        payload=completed_clinical_trials,
        run_timestamp=run_timestamp,
    )

    drugs = search_results["drugs"]
    _save_search_result(
        results_dir,
        prefix="drugs",
        label="Drugs from VICC Meta-Knowledgebase",
        payload=drugs,
        run_timestamp=run_timestamp,
    )

    control_stats = load_control_stats()
    summary = feasibility_summary(
        result,
        stats,
        treatments,
        control_stats,
        clinical_trials,
        completed_clinical_trials,
        drugs,
    )
    summary_json = json.dumps(summary, indent=2)
    print("\nFeasibility summary:")
    print(summary_json)

    summary_path = results_dir / f"feasibility_summary_{datetime.now():%Y%m%d_%H%M%S}.json"
    summary_path.write_text(summary_json + "\n", encoding="utf-8")
    print(f"Saved feasibility summary to: {summary_path}")
