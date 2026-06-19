from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from posthog import Posthog
from pydantic import BaseModel, Field

from cbioportal_search import (
    search_cbioportal_for_patients,
    search_neon_for_patient_metadata,
    search_neon_for_treatments,
)
from clinicaltrails_search import (
    search_active_clinical_trials,
    search_completed_clinical_trials,
    search_trial_sites,
)
from control_stats import load_control_stats
from feasibility_summary import feasibility_summary
from feedback_store import save_user_feedback
from main import extract_trial_eligibility
from drug_search import search_combined_drugs
from search_depmap import search_depmap_for_cell_lines
from vicc_search import search_vicc_drugs

load_dotenv()

_posthog_client: Posthog | None = None


def get_posthog() -> Posthog | None:
    return _posthog_client


def _posthog_distinct_id(request: Request, fallback: str | None = None) -> str:
    header_id = request.headers.get("x-posthog-distinct-id")
    if header_id and header_id.strip():
        return header_id.strip()
    return fallback or f"anon-{uuid.uuid4()}"


def _capture_posthog(
    ph: Posthog,
    distinct_id: str,
    event: str,
    properties: dict[str, Any] | None = None,
) -> None:
    ph.capture(distinct_id, event, properties=properties or {})
    ph.flush()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _posthog_client
    token = os.getenv("POSTHOG_PROJECT_TOKEN", "")
    host = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com")
    if token:
        _posthog_client = Posthog(
            project_api_key=token,
            host=host,
            enable_exception_autocapture=True,
        )
    yield
    if _posthog_client:
        _posthog_client.shutdown()


app = FastAPI(title="GeneTrail AI", version="0.1.0", lifespan=lifespan)

frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
extra_origins = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGIN_EXTRA", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        frontend_origin,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        *extra_origins,
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    protocol: str = Field(..., min_length=10, description="Clinical trial protocol text")


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Star rating from 1 to 5")
    comment: str = Field(default="", max_length=2000)
    email: str | None = Field(default=None, max_length=320)
    page_section: str | None = Field(default="overall", max_length=100)
    analysis_snapshot: dict[str, Any] | None = None


class FeedbackResponse(BaseModel):
    id: int
    rating: int
    comment: str
    email: str | None
    page_section: str | None
    analysis_snapshot: dict[str, Any]
    created_at: str | None


class AnalyzeResponse(BaseModel):
    eligibility: dict[str, Any]
    stats: dict[str, Any]
    treatment_stats: dict[str, Any]
    patient_metadata_stats: dict[str, Any]
    control_stats: dict[str, Any]
    clinical_trials: dict[str, Any]
    completed_clinical_trials: dict[str, Any]
    trial_sites: dict[str, Any]
    existing_drugs: dict[str, Any]
    depmap: dict[str, Any]
    feasibility_summary: dict[str, Any]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/feedback", response_model=FeedbackResponse)
def submit_feedback(payload: FeedbackRequest, request: Request) -> FeedbackResponse:
    try:
        saved = save_user_feedback(
            rating=payload.rating,
            comment=payload.comment,
            email=payload.email,
            page_section=payload.page_section,
            analysis_snapshot=payload.analysis_snapshot,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Feedback save failed: {exc}") from exc

    ph = get_posthog()
    if ph:
        distinct_id = _posthog_distinct_id(
            request,
            payload.email or f"anon-{saved.get('id', uuid.uuid4())}",
        )
        _capture_posthog(
            ph,
            distinct_id,
            "feedback_submitted",
            properties={
                "rating": payload.rating,
                "has_comment": bool(payload.comment),
                "comment_length": len(payload.comment),
                "page_section": payload.page_section,
                "has_snapshot": payload.analysis_snapshot is not None,
            },
        )

    return FeedbackResponse(**saved)


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze_protocol(payload: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    protocol = payload.protocol.strip()
    if not protocol:
        raise HTTPException(status_code=400, detail="Protocol text is required.")

    ph = get_posthog()
    distinct_id = _posthog_distinct_id(request)

    if ph:
        _capture_posthog(
            ph,
            distinct_id,
            "protocol_analyzed",
            properties={
                "protocol_length": len(protocol),
            },
        )

    try:
        eligibility = extract_trial_eligibility(protocol)
        stats = search_cbioportal_for_patients(eligibility)
        treatment_stats = search_neon_for_treatments(eligibility)
        treatment_stats.pop("patients", None)
        patient_metadata_stats = search_neon_for_patient_metadata(eligibility)
        patient_metadata_stats.pop("patients", None)
        control_stats = load_control_stats()
        clinical_trials = search_active_clinical_trials(eligibility, max_results=50)
        active_trial_sites = clinical_trials.pop("trial_sites", [])
        completed_clinical_trials = search_completed_clinical_trials(
            eligibility,
            max_results=50,
            active_trial_sites=active_trial_sites,
        )
        trial_sites = search_trial_sites(eligibility, max_results=50)
        trial_sites.pop("matched_trials", None)
        existing_drugs = search_combined_drugs(
            eligibility.required_biomarkers,
            eligibility.cancer_type,
        )
        depmap = search_depmap_for_cell_lines(eligibility, limit=0)
        depmap.pop("models", None)
        summary = feasibility_summary(
            eligibility,
            stats,
            treatment_stats,
            control_stats,
            clinical_trials,
            completed_clinical_trials,
            existing_drugs,
            patient_metadata_stats,
            depmap=depmap,
            trial_sites=trial_sites,
        )
    except RuntimeError as exc:
        if ph:
            _capture_posthog(
                ph,
                distinct_id,
                "analysis_failed",
                properties={"error_type": "runtime", "error_message": str(exc)},
            )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        if ph:
            ph.capture_exception(exc)
            ph.flush()
            _capture_posthog(
                ph,
                distinct_id,
                "analysis_failed",
                properties={"error_type": "unexpected", "error_message": str(exc)},
            )
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc

    if ph:
        overall_verdict = summary.get("overall_verdict", "")
        _capture_posthog(
            ph,
            distinct_id,
            "analysis_completed",
            properties={
                "cancer_type": eligibility.cancer_type,
                "required_biomarker_count": len(eligibility.required_biomarkers),
                "excluded_biomarker_count": len(eligibility.excluded_biomarkers),
                "eligible_patients": stats.get("eligible_patients"),
                "active_trial_count": clinical_trials.get("matched_trial_count"),
                "completed_trial_count": completed_clinical_trials.get("matched_trial_count"),
                "matched_drug_count": existing_drugs.get("matched_drug_count"),
                "eligible_cell_lines": depmap.get("eligible_cell_lines"),
                "overall_verdict_length": len(overall_verdict),
            },
        )

    return AnalyzeResponse(
        eligibility=eligibility.model_dump(),
        stats=stats,
        treatment_stats=treatment_stats,
        patient_metadata_stats=patient_metadata_stats,
        control_stats=control_stats,
        clinical_trials=clinical_trials,
        completed_clinical_trials=completed_clinical_trials,
        trial_sites=trial_sites,
        existing_drugs=existing_drugs,
        depmap=depmap,
        feasibility_summary=summary,
    )
