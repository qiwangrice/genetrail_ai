from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from cbioportal_search import search_cbioportal_for_patients
from main import extract_trial_eligibility

load_dotenv()

app = FastAPI(title="GeneTrail AI", version="0.1.0")

frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    protocol: str = Field(..., min_length=10, description="Clinical trial protocol text")


class AnalyzeResponse(BaseModel):
    eligibility: dict[str, Any]
    stats: dict[str, Any]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze_protocol(payload: AnalyzeRequest) -> AnalyzeResponse:
    protocol = payload.protocol.strip()
    if not protocol:
        raise HTTPException(status_code=400, detail="Protocol text is required.")

    try:
        eligibility = extract_trial_eligibility(protocol)
        stats = search_cbioportal_for_patients(eligibility)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc

    return AnalyzeResponse(
        eligibility=eligibility.model_dump(),
        stats=stats,
    )
