from __future__ import annotations

import os
import re
from typing import Iterable, List

import requests

ONCOKB_PRODUCTION_API_BASE = "https://www.oncokb.org/api/v1"
ONCOKB_DEMO_API_BASE = "https://demo.oncokb.org/api/v1"
FUSION_GENE_PATTERN = re.compile(
    r"([A-Z0-9]+)\s*(?:-|/|\s+)\s*([A-Z0-9]+)",
    re.IGNORECASE,
)
KNOWN_FUSION_GENES = frozenset(
    {
        "ALK",
        "ROS1",
        "RET",
        "NTRK1",
        "NTRK2",
        "NTRK3",
        "NRG1",
        "FGFR1",
        "FGFR2",
        "FGFR3",
        "MET",
        "BRAF",
    }
)


def _biomarker_gene(biomarker: str) -> str:
    return biomarker.strip().upper().split()[0]


def _normalize_tumor_type(cancer_type: str | None) -> str | None:
    if not cancer_type or not str(cancer_type).strip():
        return None
    return str(cancer_type).strip()


def _oncokb_api_base() -> str:
    configured = os.getenv("ONCOKB_API_BASE", "").strip()
    if configured:
        return configured.rstrip("/")
    if os.getenv("ONCOKB_API_TOKEN", "").strip():
        return ONCOKB_PRODUCTION_API_BASE
    return ONCOKB_DEMO_API_BASE


def _oncokb_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = os.getenv("ONCOKB_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_fusion_genes(biomarker: str) -> tuple[str, str | None]:
    text = biomarker.strip()
    upper = text.upper()
    if "FUSION" in upper:
        text = re.sub(r"\bfusion\b", "", text, flags=re.IGNORECASE).strip()
    if "REARRANGEMENT" in upper:
        text = re.sub(r"\brearrangement\b", "", text, flags=re.IGNORECASE).strip()

    match = FUSION_GENE_PATTERN.search(text)
    if match:
        return match.group(1).upper(), match.group(2).upper()

    gene = _biomarker_gene(text)
    return gene, None


def _parse_biomarker(biomarker: str) -> dict:
    text = biomarker.strip()
    upper = text.upper()

    if not text:
        return {"type": "unsupported", "reason": "empty biomarker"}

    if "AMPLIFICATION" in upper or re.search(r"\bAMP(LIFICATION)?\b", upper):
        gene = _biomarker_gene(re.sub(r"\b(amplification|amp(lification)?)\b", "", text, flags=re.IGNORECASE))
        return {
            "type": "copy_number",
            "gene": gene,
            "copy_name_alteration_type": "Amplification",
        }

    if "FUSION" in upper or "REARRANGEMENT" in upper or "-" in text or "/" in text:
        gene_a, gene_b = _parse_fusion_genes(text)
        return {
            "type": "fusion",
            "gene_a": gene_a,
            "gene_b": gene_b,
        }

    parts = text.split(None, 1)
    gene = parts[0].upper()
    alteration = parts[1].strip() if len(parts) > 1 else None

    if alteration is None and gene in KNOWN_FUSION_GENES:
        return {
            "type": "fusion",
            "gene_a": gene,
            "gene_b": None,
        }

    if alteration is None:
        return {
            "type": "unsupported",
            "reason": "specific alteration required for mutation lookup",
            "gene": gene,
        }

    return {
        "type": "mutation",
        "gene": gene,
        "alteration": alteration,
    }


def _parse_treatment(treatment: dict) -> dict:
    drugs = treatment.get("drugs") or []
    drug_names = [drug.get("drugName") for drug in drugs if drug.get("drugName")]
    associated = treatment.get("levelAssociatedCancerType") or {}
    return {
        "drug_names": drug_names,
        "drugs": [
            {
                "drug_name": drug.get("drugName"),
                "ncit_code": drug.get("ncitCode"),
            }
            for drug in drugs
            if drug.get("drugName")
        ],
        "level": treatment.get("level"),
        "fda_level": treatment.get("fdaLevel"),
        "alterations": treatment.get("alterations") or [],
        "cancer_type": associated.get("name"),
        "cancer_type_code": associated.get("code"),
        "description": treatment.get("description"),
    }


def _annotate_biomarker(
    biomarker: str,
    parsed: dict,
    tumor_type: str | None,
    api_base: str,
    headers: dict[str, str],
    timeout_seconds: int,
) -> dict:
    annotation_type = parsed.get("type")
    params: dict[str, str | bool] = {}
    endpoint = ""

    if tumor_type:
        params["tumorType"] = tumor_type

    if annotation_type == "mutation":
        endpoint = "/annotate/mutations/byProteinChange"
        params["hugoSymbol"] = parsed["gene"]
        params["alteration"] = parsed["alteration"]
    elif annotation_type == "fusion":
        endpoint = "/annotate/structuralVariants"
        params["hugoSymbolA"] = parsed["gene_a"]
        params["structuralVariantType"] = "FUSION"
        params["isFunctionalFusion"] = True
        if parsed.get("gene_b"):
            params["hugoSymbolB"] = parsed["gene_b"]
    elif annotation_type == "copy_number":
        endpoint = "/annotate/copyNumberAlterations"
        params["hugoSymbol"] = parsed["gene"]
        params["copyNameAlterationType"] = parsed["copy_name_alteration_type"]
    else:
        return {
            "biomarker": biomarker,
            "annotation_type": annotation_type,
            "parsed": parsed,
            "gene_exist": False,
            "variant_exist": False,
            "oncogenic": None,
            "highest_sensitive_level": None,
            "highest_fda_level": None,
            "treatments": [],
            "error": parsed.get("reason", "unsupported biomarker format"),
        }

    response = requests.get(
        f"{api_base}{endpoint}",
        params=params,
        headers=headers,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()

    treatments = [_parse_treatment(item) for item in payload.get("treatments") or []]
    return {
        "biomarker": biomarker,
        "annotation_type": annotation_type,
        "parsed": parsed,
        "gene_exist": payload.get("geneExist"),
        "variant_exist": payload.get("variantExist"),
        "oncogenic": payload.get("oncogenic"),
        "highest_sensitive_level": payload.get("highestSensitiveLevel"),
        "highest_fda_level": payload.get("highestFdaLevel"),
        "tumor_type_summary": payload.get("tumorTypeSummary"),
        "variant_summary": payload.get("variantSummary"),
        "treatments": treatments,
        "error": None,
    }


def _dedupe_drugs(biomarker_annotations: Iterable[dict]) -> List[dict]:
    merged: dict[tuple, dict] = {}

    for annotation in biomarker_annotations:
        biomarker = annotation.get("biomarker")
        for treatment in annotation.get("treatments") or []:
            drug_names = treatment.get("drug_names") or []
            if not drug_names:
                continue

            key = (
                tuple(sorted(name.upper() for name in drug_names)),
                treatment.get("level"),
                treatment.get("cancer_type_code"),
            )
            if key not in merged:
                merged[key] = {
                    "drug_names": drug_names,
                    "drugs": treatment.get("drugs") or [],
                    "level": treatment.get("level"),
                    "fda_level": treatment.get("fda_level"),
                    "alterations": treatment.get("alterations") or [],
                    "cancer_type": treatment.get("cancer_type"),
                    "cancer_type_code": treatment.get("cancer_type_code"),
                    "biomarkers": [biomarker],
                    "description": treatment.get("description"),
                }
                continue

            existing = merged[key]
            if biomarker not in existing["biomarkers"]:
                existing["biomarkers"].append(biomarker)

    return sorted(
        merged.values(),
        key=lambda item: (
            item.get("level") or "",
            item.get("fda_level") or "",
            ", ".join(item.get("drug_names") or []),
        ),
    )


def search_oncokb_drugs(
    required_biomarkers: Iterable[str],
    cancer_type: str | None = None,
    *,
    timeout_seconds: int = 30,
) -> dict:
    """
    Look up existing therapies in OncoKB for required biomarkers and cancer type.

    Uses OncoKB API v1 annotation endpoints:
      - /annotate/mutations/byProteinChange
      - /annotate/structuralVariants
      - /annotate/copyNumberAlterations

    Set ONCOKB_API_TOKEN for full production results. Without a token, the demo
    API is used and returns limited annotations.
    """
    biomarkers = [str(item).strip() for item in required_biomarkers if str(item).strip()]
    tumor_type = _normalize_tumor_type(cancer_type)
    api_base = _oncokb_api_base()
    headers = _oncokb_headers()

    biomarker_annotations: List[dict] = []
    for biomarker in biomarkers:
        parsed = _parse_biomarker(biomarker)
        try:
            biomarker_annotations.append(
                _annotate_biomarker(
                    biomarker,
                    parsed,
                    tumor_type,
                    api_base,
                    headers,
                    timeout_seconds,
                )
            )
        except requests.RequestException as exc:
            biomarker_annotations.append(
                {
                    "biomarker": biomarker,
                    "annotation_type": parsed.get("type"),
                    "parsed": parsed,
                    "gene_exist": False,
                    "variant_exist": False,
                    "oncogenic": None,
                    "highest_sensitive_level": None,
                    "highest_fda_level": None,
                    "treatments": [],
                    "error": str(exc),
                }
            )

    matched_drugs = _dedupe_drugs(biomarker_annotations)
    return {
        "data_source": "oncokb_api_v1",
        "api_base": api_base,
        "uses_production_api": api_base.startswith(ONCOKB_PRODUCTION_API_BASE),
        "cancer_type": tumor_type,
        "required_biomarkers": biomarkers,
        "biomarker_annotation_count": len(biomarker_annotations),
        "matched_drug_count": len(matched_drugs),
        "biomarker_annotations": biomarker_annotations,
        "matched_drugs": matched_drugs,
    }


if __name__ == "__main__":
    import json

    demo = search_oncokb_drugs(
        required_biomarkers=["KRAS G12C"],
        cancer_type="Non-Small Cell Lung Cancer",
    )
    print(json.dumps(demo, indent=2))
