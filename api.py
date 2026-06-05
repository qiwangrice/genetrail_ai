from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from cbioportal_search import (
    search_cbioportal_for_patients,
    search_neon_for_patient_metadata,
    search_neon_for_treatments,
)
from clinicaltrails_search import (
    search_active_clinical_trials,
    search_completed_clinical_trials,
)
from control_stats import load_control_stats
from feasibility_summary import feasibility_summary
from feedback_store import save_user_feedback
from main import extract_trial_eligibility
from vicc_search import search_vicc_drugs

load_dotenv()

app = FastAPI(title="GeneTrail AI", version="0.1.0")

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
    existing_drugs: dict[str, Any]
    feasibility_summary: dict[str, Any]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/feedback", response_model=FeedbackResponse)
def submit_feedback(payload: FeedbackRequest) -> FeedbackResponse:
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

    return FeedbackResponse(**saved)


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze_protocol(payload: AnalyzeRequest) -> AnalyzeResponse:
    protocol = payload.protocol.strip()
    if not protocol:
        raise HTTPException(status_code=400, detail="Protocol text is required.")

    try:
        eligibility = extract_trial_eligibility(protocol)
        stats = search_cbioportal_for_patients(eligibility)
        treatment_stats = search_neon_for_treatments(eligibility)
        treatment_stats.pop("patients", None)
        patient_metadata_stats = search_neon_for_patient_metadata(eligibility)
        patient_metadata_stats.pop("patients", None)
        control_stats = load_control_stats()
        clinical_trials = search_active_clinical_trials(eligibility, max_results=50)
        completed_clinical_trials = search_completed_clinical_trials(
            eligibility,
            max_results=50,
        )
        existing_drugs = search_vicc_drugs(
            eligibility.required_biomarkers,
            eligibility.cancer_type,
        )
        summary = feasibility_summary(
            eligibility,
            stats,
            treatment_stats,
            control_stats,
            clinical_trials,
            completed_clinical_trials,
            existing_drugs,
            patient_metadata_stats,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc

    return AnalyzeResponse(
        eligibility=eligibility.model_dump(),
        stats=stats,
        treatment_stats=treatment_stats,
        patient_metadata_stats=patient_metadata_stats,
        control_stats=control_stats,
        clinical_trials=clinical_trials,
        completed_clinical_trials=completed_clinical_trials,
        existing_drugs=existing_drugs,
        feasibility_summary=summary,
    )
