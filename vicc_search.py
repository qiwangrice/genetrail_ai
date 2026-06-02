from __future__ import annotations

import re
from typing import Iterable, List

import requests

VICC_API_BASE = "https://search.cancervariants.org/api/v1"
VICC_ASSOCIATIONS_URL = f"{VICC_API_BASE}/associations"
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
SKIP_DRUG_PATTERN = re.compile(r"^[\d\-]+$")
VARIANT_TOKEN_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*$")


def _biomarker_gene(biomarker: str) -> str:
    return biomarker.strip().upper().split()[0]


def _normalize_cancer_type(cancer_type: str | None) -> str | None:
    if not cancer_type or not str(cancer_type).strip():
        return None
    return str(cancer_type).strip()


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
        gene = _biomarker_gene(
            re.sub(r"\b(amplification|amp(lification)?)\b", "", text, flags=re.IGNORECASE)
        )
        return {"type": "copy_number", "gene": gene}

    if "FUSION" in upper or "REARRANGEMENT" in upper or "-" in text or "/" in text:
        gene_a, gene_b = _parse_fusion_genes(text)
        return {"type": "fusion", "gene_a": gene_a, "gene_b": gene_b}

    parts = text.split(None, 1)
    gene = parts[0].upper()
    alteration = parts[1].strip() if len(parts) > 1 else None

    if alteration is None and gene in KNOWN_FUSION_GENES:
        return {"type": "fusion", "gene_a": gene, "gene_b": None}

    if alteration is None:
        return {"type": "gene", "gene": gene}

    return {"type": "mutation", "gene": gene, "alteration": alteration}


def _build_vicc_query(biomarker: str, cancer_type: str | None) -> str | None:
    parsed = _parse_biomarker(biomarker)
    query_parts: List[str] = []

    if parsed["type"] == "mutation":
        query_parts.append(f"genes:{parsed['gene']}")
        alteration = parsed["alteration"]
        first_token = alteration.split()[0]
        if VARIANT_TOKEN_PATTERN.match(first_token.upper()):
            query_parts.append(f"feature_names:{first_token}")
        else:
            query_parts.append(f'"{alteration}"')
    elif parsed["type"] == "fusion":
        query_parts.append(f"genes:{parsed['gene_a']}")
        query_parts.append("fusion")
        if parsed.get("gene_b"):
            query_parts.append(parsed["gene_b"])
    elif parsed["type"] == "copy_number":
        query_parts.append(f"genes:{parsed['gene']}")
        query_parts.append("amplification")
    elif parsed["type"] == "gene":
        query_parts.append(f"genes:{parsed['gene']}")
    else:
        return None

    if cancer_type:
        query_parts.append(cancer_type)

    return " AND ".join(query_parts)


def _split_drug_labels(raw_labels: str | None) -> List[str]:
    if not raw_labels or not str(raw_labels).strip():
        return []

    labels: List[str] = []
    for label in re.split(r"[,/]", str(raw_labels)):
        cleaned = label.strip()
        if not cleaned or SKIP_DRUG_PATTERN.match(cleaned):
            continue
        labels.append(cleaned)
    return labels


def _parse_association_hit(hit: dict) -> dict | None:
    association = hit.get("association") or {}
    drug_names = _split_drug_labels(association.get("drug_labels") or hit.get("drugs"))
    if not drug_names:
        return None

    evidence_label = association.get("evidence_label") or hit.get("evidence_label")
    response_type = association.get("response_type")
    level = evidence_label
    if response_type:
        level = f"{evidence_label} ({response_type})" if evidence_label else str(response_type)

    return {
        "drug_names": drug_names,
        "drugs": [{"drug_name": name, "ncit_code": None} for name in drug_names],
        "level": level,
        "evidence_label": evidence_label,
        "evidence_level": association.get("evidence_level"),
        "response_type": response_type,
        "alterations": [hit.get("feature_names")] if hit.get("feature_names") else [],
        "cancer_type": hit.get("diseases") or association.get("disease_labels_truncated"),
        "description": association.get("description"),
        "publication_url": association.get("publication_url"),
        "source_link": association.get("source_link"),
    }


def _search_associations(
    query: str,
    *,
    max_results: int,
    timeout_seconds: int,
) -> tuple[List[dict], int]:
    response = requests.get(
        VICC_ASSOCIATIONS_URL,
        params={
            "q": query,
            "limit": min(max(max_results, 1), 100),
            "offset": 0,
        },
        headers={"Accept": "application/json"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    hits = payload.get("hits") or {}
    total = hits.get("total") or 0

    associations: List[dict] = []
    for hit in hits.get("hits") or []:
        parsed = _parse_association_hit(hit)
        if parsed:
            associations.append(parsed)
        if len(associations) >= max_results:
            break

    return associations, total


def _search_biomarker(
    biomarker: str,
    cancer_type: str | None,
    *,
    max_results: int,
    timeout_seconds: int,
) -> dict:
    parsed = _parse_biomarker(biomarker)
    query = _build_vicc_query(biomarker, cancer_type)
    if not query:
        return {
            "biomarker": biomarker,
            "annotation_type": parsed.get("type"),
            "parsed": parsed,
            "query": None,
            "association_count": 0,
            "total_hits": 0,
            "treatments": [],
            "error": parsed.get("reason", "unsupported biomarker format"),
        }

    try:
        treatments, total_hits = _search_associations(
            query,
            max_results=max_results,
            timeout_seconds=timeout_seconds,
        )
        return {
            "biomarker": biomarker,
            "annotation_type": parsed.get("type"),
            "parsed": parsed,
            "query": query,
            "association_count": len(treatments),
            "total_hits": total_hits,
            "treatments": treatments,
            "error": None,
        }
    except requests.RequestException as exc:
        return {
            "biomarker": biomarker,
            "annotation_type": parsed.get("type"),
            "parsed": parsed,
            "query": query,
            "association_count": 0,
            "total_hits": 0,
            "treatments": [],
            "error": str(exc),
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
                treatment.get("cancer_type"),
            )
            if key not in merged:
                merged[key] = {
                    "drug_names": drug_names,
                    "drugs": treatment.get("drugs") or [],
                    "level": treatment.get("level"),
                    "evidence_label": treatment.get("evidence_label"),
                    "evidence_level": treatment.get("evidence_level"),
                    "response_type": treatment.get("response_type"),
                    "alterations": treatment.get("alterations") or [],
                    "cancer_type": treatment.get("cancer_type"),
                    "biomarkers": [biomarker],
                    "description": treatment.get("description"),
                    "publication_url": treatment.get("publication_url"),
                    "source_link": treatment.get("source_link"),
                }
                continue

            existing = merged[key]
            if biomarker not in existing["biomarkers"]:
                existing["biomarkers"].append(biomarker)

    return sorted(
        merged.values(),
        key=lambda item: (
            item.get("evidence_label") or "",
            item.get("response_type") or "",
            ", ".join(item.get("drug_names") or []),
        ),
    )


def search_vicc_drugs(
    required_biomarkers: Iterable[str],
    cancer_type: str | None = None,
    *,
    max_results_per_biomarker: int = 50,
    timeout_seconds: int = 30,
) -> dict:
    """
    Look up existing therapies from the VICC Meta-Knowledgebase for biomarkers
    and cancer type.

    API docs: https://search.cancervariants.org/api/v1/ui/#/Associations
    """
    biomarkers = [str(item).strip() for item in required_biomarkers if str(item).strip()]
    normalized_cancer_type = _normalize_cancer_type(cancer_type)

    biomarker_annotations = [
        _search_biomarker(
            biomarker,
            normalized_cancer_type,
            max_results=max_results_per_biomarker,
            timeout_seconds=timeout_seconds,
        )
        for biomarker in biomarkers
    ]
    matched_drugs = _dedupe_drugs(biomarker_annotations)

    return {
        "data_source": "vicc_metakb_api_v1",
        "api_base": VICC_API_BASE,
        "search_url": VICC_ASSOCIATIONS_URL,
        "cancer_type": normalized_cancer_type,
        "required_biomarkers": biomarkers,
        "biomarker_annotation_count": len(biomarker_annotations),
        "matched_drug_count": len(matched_drugs),
        "biomarker_annotations": biomarker_annotations,
        "matched_drugs": matched_drugs,
    }


DEMO_VICC_EXAMPLE = {
    "required_biomarkers": ["KRAS G12C"],
    "cancer_type": "non-small cell lung cancer",
}


if __name__ == "__main__":
    import json

    demo = search_vicc_drugs(**DEMO_VICC_EXAMPLE)
    print(json.dumps(demo, indent=2))
