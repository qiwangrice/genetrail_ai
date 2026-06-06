from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable, List

import requests

from vicc_search import (
    CIVICDB_BASE,
    SKIP_DRUG_PATTERN,
    _mutation_type_badges,
    _normalize_biomarker_genes,
    _normalize_cancer_type,
    _parse_biomarker,
    _required_variant_tokens,
    _unique_strings,
)

CIVIC_GRAPHQL_URL = f"{CIVICDB_BASE}/api/graphql"
CIVIC_AI_INTEGRATIONS_URL = f"{CIVICDB_BASE}/pages/ai-integrations"

EVIDENCE_ITEMS_QUERY = """
query CivicEvidenceItems(
  $molecularProfileName: String
  $molecularProfileId: Int
  $diseaseName: String
  $first: Int
) {
  evidenceItems(
    molecularProfileName: $molecularProfileName
    molecularProfileId: $molecularProfileId
    diseaseName: $diseaseName
    status: ACCEPTED
    first: $first
  ) {
    totalCount
    nodes {
      id
      name
      status
      evidenceLevel
      evidenceType
      significance
      description
      link
      therapies {
        name
      }
      disease {
        name
      }
      molecularProfile {
        id
        name
        link
      }
      source {
        citationId
        sourceType
      }
    }
  }
}
"""

BROWSE_MOLECULAR_PROFILES_QUERY = """
query CivicBrowseMolecularProfiles(
  $featureName: String
  $variantName: String
  $molecularProfileName: String
  $first: Int
) {
  browseMolecularProfiles(
    featureName: $featureName
    variantName: $variantName
    molecularProfileName: $molecularProfileName
    first: $first
  ) {
    totalCount
    nodes {
      id
      name
      link
    }
  }
}
"""


def _civic_absolute_url(path: str | None) -> str | None:
    if not path or not str(path).strip():
        return None
    text = str(path).strip()
    if text.lower().startswith("http"):
        return text
    if not text.startswith("/"):
        text = f"/{text}"
    return f"{CIVICDB_BASE}{text}"


def _civic_publication_url(source: dict | None) -> str | None:
    if not source:
        return None
    citation_id = str(source.get("citationId") or "").strip()
    if not citation_id or not citation_id.isdigit():
        return None
    return f"https://pubmed.ncbi.nlm.nih.gov/{citation_id}/"


def _civic_disease_filter(cancer_type: str | None) -> str | None:
    if not cancer_type:
        return None
    text = cancer_type.strip().lower()
    if "lung" in text or "nsclc" in text:
        return "Lung"
    if "colorectal" in text or "colon" in text:
        return "Colorectal"
    if "breast" in text:
        return "Breast"
    if "melanoma" in text:
        return "Melanoma"
    return None


def _civic_molecular_profile_name(parsed: dict, source_biomarker: str) -> str | None:
    biomarker_type = parsed.get("type")
    if biomarker_type == "mutation":
        gene = parsed.get("gene")
        variants = _required_variant_tokens(parsed)
        if gene and variants:
            return f"{gene} {variants[0]}"
        if gene:
            return f"{gene} Mutation"
    if biomarker_type == "copy_number" and parsed.get("gene"):
        return f"{parsed['gene']} Amplification"
    if biomarker_type == "fusion" and parsed.get("gene_a"):
        if parsed.get("gene_b"):
            return f"{parsed['gene_a']}-{parsed['gene_b']} Fusion"
        return f"{parsed['gene_a']} Fusion"

    text = source_biomarker.strip()
    return text or None


def _civic_graphql(
    query: str,
    variables: dict[str, Any],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    response = requests.post(
        CIVIC_GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        messages = "; ".join(
            str(error.get("message") or error) for error in payload["errors"]
        )
        raise RuntimeError(f"CIViC GraphQL error: {messages}")
    return payload.get("data") or {}


def _civic_feature_and_variant(parsed: dict) -> tuple[str | None, str | None]:
    biomarker_type = parsed.get("type")
    if biomarker_type == "mutation":
        variants = _required_variant_tokens(parsed)
        return parsed.get("gene"), variants[0] if variants else None
    if biomarker_type == "gene":
        return parsed.get("gene"), None
    if biomarker_type == "fusion":
        return parsed.get("gene_a"), None
    if biomarker_type == "copy_number":
        return parsed.get("gene"), "Amplification"
    return None, None


def _parse_civic_evidence_node(node: dict) -> dict | None:
    therapies = [
        str(therapy.get("name") or "").strip()
        for therapy in (node.get("therapies") or [])
        if therapy.get("name")
    ]
    therapies = [name for name in therapies if name and not SKIP_DRUG_PATTERN.match(name)]
    if not therapies:
        return None

    molecular_profile = node.get("molecularProfile") or {}
    source = node.get("source") or {}
    evidence_level = node.get("evidenceLevel")
    significance = node.get("significance")
    level = evidence_level
    if significance:
        level = f"{evidence_level} ({significance})" if evidence_level else str(significance)

    evidence_url = _civic_absolute_url(node.get("link"))
    profile_url = _civic_absolute_url(molecular_profile.get("link"))
    publication_url = _civic_publication_url(source)

    return {
        "drug_names": therapies,
        "drugs": [
            {
                "drug_name": name,
                "ncit_code": None,
                "url": evidence_url or profile_url,
            }
            for name in therapies
        ],
        "level": level,
        "evidence_label": evidence_level,
        "evidence_level": evidence_level,
        "response_type": significance,
        "alterations": [molecular_profile.get("name")] if molecular_profile.get("name") else [],
        "cancer_type": (node.get("disease") or {}).get("name"),
        "description": node.get("description"),
        "publication_url": publication_url,
        "source_link": profile_url,
        "url": evidence_url or profile_url or publication_url,
        "civic_evidence_id": node.get("id"),
        "civic_evidence_name": node.get("name"),
        "civic_molecular_profile_id": molecular_profile.get("id"),
        "civic_molecular_profile_name": molecular_profile.get("name"),
        "data_source": "civic_graphql",
    }


def _fetch_civic_evidence_items(
    *,
    molecular_profile_name: str | None = None,
    molecular_profile_id: int | None = None,
    disease_name: str | None = None,
    max_results: int,
    timeout_seconds: int,
) -> tuple[List[dict], int]:
    if not molecular_profile_name and molecular_profile_id is None:
        return [], 0

    # CIViC treats explicit null filters as active constraints and returns no rows.
    # Only send variables that are actually set.
    variables: dict[str, Any] = {
        "first": max(1, min(max_results, 100)),
    }
    if molecular_profile_name:
        variables["molecularProfileName"] = molecular_profile_name
    if molecular_profile_id is not None:
        variables["molecularProfileId"] = molecular_profile_id
    if disease_name:
        variables["diseaseName"] = disease_name

    data = _civic_graphql(
        EVIDENCE_ITEMS_QUERY,
        variables,
        timeout_seconds=timeout_seconds,
    )
    evidence_items = data.get("evidenceItems") or {}
    total_count = evidence_items.get("totalCount") or 0
    treatments: List[dict] = []
    for node in evidence_items.get("nodes") or []:
        parsed = _parse_civic_evidence_node(node)
        if parsed:
            treatments.append(parsed)
    return treatments, total_count


def _discover_civic_profile_ids(
    *,
    feature_name: str | None,
    variant_name: str | None,
    molecular_profile_name: str | None,
    max_profiles: int,
    timeout_seconds: int,
) -> List[dict]:
    variables: dict[str, Any] = {
        "first": max(1, min(max_profiles, 25)),
    }
    if feature_name:
        variables["featureName"] = feature_name
    if variant_name:
        variables["variantName"] = variant_name
    if molecular_profile_name:
        variables["molecularProfileName"] = molecular_profile_name

    data = _civic_graphql(
        BROWSE_MOLECULAR_PROFILES_QUERY,
        variables,
        timeout_seconds=timeout_seconds,
    )
    return (data.get("browseMolecularProfiles") or {}).get("nodes") or []


def _search_civic_biomarker(
    source_biomarker: str,
    cancer_type: str | None,
    *,
    max_results: int,
    timeout_seconds: int,
) -> dict:
    parsed = _parse_biomarker(source_biomarker)
    if parsed.get("type") == "unsupported":
        return {
            "biomarker": source_biomarker,
            "source_biomarker": source_biomarker,
            "search_mode": "unsupported",
            "annotation_type": parsed.get("type"),
            "parsed": parsed,
            "query": None,
            "association_count": 0,
            "total_hits": 0,
            "treatments": [],
            "mutation_type_badges": _mutation_type_badges(
                {"parsed": parsed, "annotation_type": parsed.get("type")}
            ),
            "error": parsed.get("reason", "unsupported biomarker format"),
        }

    disease_name = _civic_disease_filter(cancer_type)
    profile_name = _civic_molecular_profile_name(parsed, source_biomarker)
    feature_name, variant_name = _civic_feature_and_variant(parsed)
    search_mode = "molecular_profile"
    if feature_name and variant_name:
        search_mode = "feature_variant"
    elif feature_name:
        search_mode = "feature"

    try:
        treatments: List[dict] = []
        total_hits = 0
        queries: List[str] = []

        if profile_name:
            queries.append(f'molecularProfileName:"{profile_name}"')
            profile_treatments, profile_total = _fetch_civic_evidence_items(
                molecular_profile_name=profile_name,
                disease_name=disease_name,
                max_results=max_results,
                timeout_seconds=timeout_seconds,
            )
            treatments.extend(profile_treatments)
            total_hits = max(total_hits, profile_total)

        if len(treatments) < max_results and (feature_name or profile_name):
            profiles = _discover_civic_profile_ids(
                feature_name=feature_name,
                variant_name=variant_name,
                molecular_profile_name=profile_name,
                max_profiles=10,
                timeout_seconds=timeout_seconds,
            )
            seen_evidence_ids = {
                treatment.get("civic_evidence_id")
                for treatment in treatments
                if treatment.get("civic_evidence_id") is not None
            }
            for profile in profiles:
                if len(treatments) >= max_results:
                    break
                profile_id = profile.get("id")
                if profile_id is None:
                    continue
                queries.append(f"molecularProfileId:{profile_id}")
                extra_treatments, extra_total = _fetch_civic_evidence_items(
                    molecular_profile_id=int(profile_id),
                    disease_name=disease_name,
                    max_results=max_results - len(treatments),
                    timeout_seconds=timeout_seconds,
                )
                total_hits = max(total_hits, extra_total)
                for treatment in extra_treatments:
                    evidence_id = treatment.get("civic_evidence_id")
                    if evidence_id in seen_evidence_ids:
                        continue
                    seen_evidence_ids.add(evidence_id)
                    treatments.append(treatment)

        return {
            "biomarker": source_biomarker,
            "source_biomarker": source_biomarker,
            "search_mode": search_mode,
            "annotation_type": parsed.get("type"),
            "parsed": parsed,
            "query": " | ".join(queries) or profile_name,
            "association_count": len(treatments),
            "total_hits": total_hits,
            "treatments": treatments[:max_results],
            "mutation_type_badges": _mutation_type_badges(
                {"parsed": parsed, "annotation_type": parsed.get("type")}
            ),
            "error": None,
        }
    except (requests.RequestException, RuntimeError) as exc:
        return {
            "biomarker": source_biomarker,
            "source_biomarker": source_biomarker,
            "search_mode": search_mode,
            "annotation_type": parsed.get("type"),
            "parsed": parsed,
            "query": profile_name,
            "association_count": 0,
            "total_hits": 0,
            "treatments": [],
            "mutation_type_badges": _mutation_type_badges(
                {"parsed": parsed, "annotation_type": parsed.get("type")}
            ),
            "error": str(exc),
        }


def _dedupe_civic_drugs(biomarker_annotations: Iterable[dict]) -> List[dict]:
    merged: dict[tuple, dict] = {}

    for annotation in biomarker_annotations:
        biomarker = annotation.get("biomarker")
        source_biomarker = annotation.get("source_biomarker") or biomarker
        for treatment in annotation.get("treatments") or []:
            drug_names = treatment.get("drug_names") or []
            if not drug_names:
                continue

            key = (
                tuple(sorted(name.upper() for name in drug_names)),
                treatment.get("civic_evidence_id"),
                treatment.get("evidence_label"),
                treatment.get("response_type"),
                treatment.get("cancer_type"),
            )
            if key in merged:
                continue

            merged[key] = {
                **treatment,
                "biomarkers": [biomarker] if biomarker else [],
                "source_biomarkers": [source_biomarker] if source_biomarker else [],
                "data_source": "civic_graphql",
            }

    return sorted(
        merged.values(),
        key=lambda item: (
            item.get("evidence_label") or "",
            item.get("response_type") or "",
            ", ".join(item.get("drug_names") or []),
        ),
    )


def search_civic_drugs(
    required_biomarkers: Iterable[str],
    cancer_type: str | None = None,
    *,
    max_results_per_biomarker: int = 50,
    timeout_seconds: int = 30,
) -> dict:
    """
    Look up therapies from CIViC using the public GraphQL API.

    CIViC documents programmatic access and AI integrations at:
    https://civicdb.org/pages/ai-integrations
    """
    normalized_cancer_type = _normalize_cancer_type(cancer_type)
    raw_biomarkers = _unique_strings(required_biomarkers)
    normalized_genes = _normalize_biomarker_genes(raw_biomarkers)

    biomarker_annotations: List[dict] = []
    if raw_biomarkers:
        with ThreadPoolExecutor(max_workers=min(len(raw_biomarkers), 6)) as executor:
            futures = [
                executor.submit(
                    _search_civic_biomarker,
                    biomarker,
                    normalized_cancer_type,
                    max_results=max_results_per_biomarker,
                    timeout_seconds=timeout_seconds,
                )
                for biomarker in raw_biomarkers
            ]
            for future in as_completed(futures):
                biomarker_annotations.append(future.result())

        biomarker_annotations.sort(
            key=lambda item: (item.get("source_biomarker") or "", item.get("biomarker") or "")
        )

    matched_drugs = _dedupe_civic_drugs(biomarker_annotations)

    return {
        "data_source": "civic_graphql",
        "api_base": CIVIC_GRAPHQL_URL,
        "integrations_url": CIVIC_AI_INTEGRATIONS_URL,
        "cancer_type": normalized_cancer_type,
        "required_biomarkers": raw_biomarkers,
        "required_biomarkers_normalized": normalized_genes,
        "search_count": len(raw_biomarkers),
        "biomarker_annotation_count": len(biomarker_annotations),
        "matched_drug_count": len(matched_drugs),
        "biomarker_annotations": biomarker_annotations,
        "matched_drugs": matched_drugs,
    }


CivicRecord = dict

DEMO_CIVIC_EXAMPLE = {
    "required_biomarkers": ["KRAS mutation"],
    "cancer_type": "non-small cell lung cancer",
}

if __name__ == "__main__":
    import json

    demo = search_civic_drugs(**DEMO_CIVIC_EXAMPLE)
    print(json.dumps(demo, indent=2))
