from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
NON_VARIANT_TOKENS = frozenset(
    {
        "MUTATION",
        "MUTATIONS",
        "MUTANT",
        "MUTATED",
        "ACTIVATING",
        "INACTIVATING",
        "ALTERATION",
        "VARIANT",
        "FUSION",
        "REARRANGEMENT",
        "AMPLIFICATION",
        "AMP",
        "WILD",
        "TYPE",
        "WT",
        "POSITIVE",
        "NEGATIVE",
    }
)


def _biomarker_gene(biomarker: str) -> str:
    return biomarker.strip().upper().split()[0]


def _normalize_biomarker_genes(biomarkers: Iterable[str]) -> List[str]:
    """Reduce biomarker strings to unique HUGO gene symbols (no variant detail)."""
    seen: set[str] = set()
    normalized: List[str] = []
    for biomarker in biomarkers:
        if not biomarker:
            continue
        gene = _biomarker_gene(biomarker)
        if gene in seen:
            continue
        seen.add(gene)
        normalized.append(gene)
    return normalized


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


def _association_reference_url(
    source_link: str | None = None,
    publication_url: str | list | None = None,
) -> str | None:
    """Prefer CIVIC/source link; fall back to first publication URL."""
    if source_link and str(source_link).strip().lower().startswith("http"):
        return str(source_link).strip()

    if isinstance(publication_url, list):
        for item in publication_url:
            text = str(item).strip() if item is not None else ""
            if text.lower().startswith("http"):
                return text
    elif publication_url and str(publication_url).strip().lower().startswith("http"):
        return str(publication_url).strip()

    return None


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


def _required_variant_tokens(parsed: dict) -> List[str]:
    """Variant tokens (e.g. G12C) required when biomarker is mutation-type."""
    if parsed.get("type") != "mutation":
        return []

    alteration = (parsed.get("alteration") or "").strip()
    if not alteration:
        return []

    def _is_variant_token(candidate: str) -> bool:
        token = candidate.strip().upper()
        return (
            bool(token)
            and token not in NON_VARIANT_TOKENS
            and VARIANT_TOKEN_PATTERN.match(token)
            and any(char.isdigit() for char in token)
        )

    tokens: List[str] = []
    for part in alteration.replace(",", " ").split():
        candidate = part.strip().upper()
        if _is_variant_token(candidate) and candidate not in tokens:
            tokens.append(candidate)

    first = alteration.split()[0].upper()
    if _is_variant_token(first) and first not in tokens:
        tokens.append(first)

    return tokens


def _hit_variant_tokens(treatment: dict) -> set[str]:
    tokens: set[str] = set()
    for alteration in treatment.get("alterations") or []:
        if alteration is None:
            continue
        text = str(alteration).strip().upper()
        if not text:
            continue
        tokens.add(text)
        for match in re.finditer(r"\b[A-Z][0-9]+[A-Z]?\b", text):
            tokens.add(match.group(0))
    return tokens


def _treatment_matches_biomarker(
    parsed: dict,
    treatment: dict,
    *,
    search_mode: str,
) -> bool:
    """
    Keep treatments that match the biomarker intent.

    Gene-level searches accept all hits from the query. Variant-level searches
    (search_mode mutation) require matching feature_names on the hit.
    """
    if search_mode != "mutation" or parsed.get("type") != "mutation":
        return True

    required_variants = _required_variant_tokens(parsed)
    if not required_variants:
        return True

    hit_variants = _hit_variant_tokens(treatment)
    if not hit_variants:
        return False

    return any(variant in hit_variants for variant in required_variants)


def _treatment_mutation_badges(
    parsed: dict,
    treatment: dict,
    *,
    search_mode: str,
) -> List[str]:
    badges = _mutation_type_badges({"parsed": parsed, "annotation_type": parsed.get("type")})
    if search_mode == "gene" or parsed.get("type") != "mutation":
        return badges

    required_variants = _required_variant_tokens(parsed)
    hit_variants = _hit_variant_tokens(treatment)
    if required_variants:
        return sorted(variant for variant in required_variants if variant in hit_variants)
    return badges


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

    publication_url = association.get("publication_url")
    source_link = association.get("source_link")
    reference_url = _association_reference_url(source_link, publication_url)

    return {
        "drug_names": drug_names,
        "drugs": [
            {
                "drug_name": name,
                "ncit_code": None,
                "url": reference_url,
            }
            for name in drug_names
        ],
        "level": level,
        "evidence_label": evidence_label,
        "evidence_level": association.get("evidence_level"),
        "response_type": response_type,
        "alterations": [hit.get("feature_names")] if hit.get("feature_names") else [],
        "cancer_type": hit.get("diseases") or association.get("disease_labels_truncated"),
        "description": association.get("description"),
        "publication_url": publication_url,
        "source_link": source_link,
        "url": reference_url,
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


def _mutation_type_badges(annotation: dict) -> List[str]:
    parsed = annotation.get("parsed") or {}
    annotation_type = annotation.get("annotation_type") or parsed.get("type")
    badges: List[str] = []

    if annotation_type == "mutation":
        alteration = (parsed.get("alteration") or "").strip()
        if alteration:
            token = alteration.split()[0]
            if VARIANT_TOKEN_PATTERN.match(token.upper()):
                badges.append(token.upper())
            else:
                badges.append(alteration)
        else:
            badges.append("Mutation")
    elif annotation_type == "fusion":
        badges.append("Fusion")
    elif annotation_type == "copy_number":
        badges.append("Amplification")

    return badges


def _unique_strings(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _collect_vicc_search_targets(
    required_biomarkers: Iterable[str],
    cancer_type: str | None,
) -> tuple[List[dict], List[str], List[str]]:
    """
    Build one VICC query per required biomarker.

    When a variant is present (e.g. KRAS G12C mutation), use a variant-specific
    VICC query and filter hits to that variant. When only the gene is specified
    (e.g. KRAS), search at gene level.
    """
    raw_biomarkers = _unique_strings(required_biomarkers)
    normalized_genes = _normalize_biomarker_genes(raw_biomarkers)
    targets: List[dict] = []
    seen_queries: set[str] = set()

    for source_biomarker in raw_biomarkers:
        parsed = _parse_biomarker(source_biomarker)
        biomarker_type = parsed.get("type")
        if biomarker_type == "unsupported":
            continue

        if biomarker_type == "gene":
            query_biomarker = parsed["gene"]
            search_mode = "gene"
        elif biomarker_type == "mutation" and _required_variant_tokens(parsed):
            query_biomarker = source_biomarker
            search_mode = "mutation"
        elif biomarker_type == "mutation":
            query_biomarker = parsed["gene"]
            search_mode = "gene"
        else:
            query_biomarker = source_biomarker
            search_mode = biomarker_type

        query = _build_vicc_query(query_biomarker, cancer_type)
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)
        targets.append(
            {
                "search_mode": search_mode,
                "biomarker": query_biomarker,
                "source_biomarker": source_biomarker,
                "parsed": parsed,
                "query": query,
            }
        )

    return targets, raw_biomarkers, normalized_genes


def _search_biomarker(
    biomarker: str,
    cancer_type: str | None,
    *,
    search_mode: str = "gene",
    source_biomarker: str | None = None,
    parsed: dict | None = None,
    max_results: int,
    timeout_seconds: int,
) -> dict:
    parsed = parsed or _parse_biomarker(biomarker)
    query = _build_vicc_query(biomarker, cancer_type)
    if not query:
        return {
            "biomarker": biomarker,
            "source_biomarker": source_biomarker or biomarker,
            "search_mode": search_mode,
            "annotation_type": parsed.get("type"),
            "parsed": parsed,
            "query": None,
            "association_count": 0,
            "total_hits": 0,
            "filtered_out_count": 0,
            "treatments": [],
            "mutation_type_badges": _mutation_type_badges(
                {"parsed": parsed, "annotation_type": parsed.get("type")}
            ),
            "error": parsed.get("reason", "unsupported biomarker format"),
        }

    try:
        treatments, total_hits = _search_associations(
            query,
            max_results=max_results,
            timeout_seconds=timeout_seconds,
        )
        filtered_out_count = 0
        matched_treatments: List[dict] = []
        for treatment in treatments:
            if _treatment_matches_biomarker(parsed, treatment, search_mode=search_mode):
                enriched = dict(treatment)
                enriched["mutation_type_badges"] = _treatment_mutation_badges(
                    parsed, treatment, search_mode=search_mode
                )
                matched_treatments.append(enriched)
            else:
                filtered_out_count += 1

        annotation = {
            "biomarker": biomarker,
            "source_biomarker": source_biomarker or biomarker,
            "search_mode": search_mode,
            "annotation_type": parsed.get("type"),
            "parsed": parsed,
            "query": query,
            "association_count": len(matched_treatments),
            "total_hits": total_hits,
            "filtered_out_count": filtered_out_count,
            "treatments": matched_treatments,
            "mutation_type_badges": _mutation_type_badges(
                {"parsed": parsed, "annotation_type": parsed.get("type")}
            ),
            "error": None,
        }
        return annotation
    except requests.RequestException as exc:
        return {
            "biomarker": biomarker,
            "source_biomarker": source_biomarker or biomarker,
            "search_mode": search_mode,
            "annotation_type": parsed.get("type"),
            "parsed": parsed,
            "query": query,
            "association_count": 0,
            "total_hits": 0,
            "filtered_out_count": 0,
            "treatments": [],
            "mutation_type_badges": _mutation_type_badges(
                {"parsed": parsed, "annotation_type": parsed.get("type")}
            ),
            "error": str(exc),
        }


def _dedupe_drugs(biomarker_annotations: Iterable[dict]) -> List[dict]:
    merged: dict[tuple, dict] = {}

    for annotation in biomarker_annotations:
        biomarker = annotation.get("biomarker")
        source_biomarker = annotation.get("source_biomarker") or biomarker
        search_mode = annotation.get("search_mode")
        for treatment in annotation.get("treatments") or []:
            type_badges = treatment.get("mutation_type_badges") or _mutation_type_badges(
                annotation
            )
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
                    "biomarkers": [biomarker] if biomarker else [],
                    "source_biomarkers": [source_biomarker] if source_biomarker else [],
                    "search_modes": [search_mode] if search_mode else [],
                    "mutation_type_badges": list(type_badges),
                    "description": treatment.get("description"),
                    "publication_url": treatment.get("publication_url"),
                    "source_link": treatment.get("source_link"),
                    "url": treatment.get("url"),
                }
                continue

            existing = merged[key]
            if not existing.get("url") and treatment.get("url"):
                existing["url"] = treatment.get("url")
            if biomarker and biomarker not in existing["biomarkers"]:
                existing["biomarkers"].append(biomarker)
            if source_biomarker and source_biomarker not in existing["source_biomarkers"]:
                existing["source_biomarkers"].append(source_biomarker)
            if search_mode and search_mode not in existing["search_modes"]:
                existing["search_modes"].append(search_mode)
            for badge in type_badges:
                if badge not in existing["mutation_type_badges"]:
                    existing["mutation_type_badges"].append(badge)

    for item in merged.values():
        item["mutation_type_badges"] = sorted(item.get("mutation_type_badges") or [])

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

    If a required biomarker includes a variant (e.g. KRAS G12C mutation), VICC
    is queried with that variant and only matching drugs are kept. If no variant
    is present (e.g. KRAS), search uses the gene symbol only.

    API docs: https://search.cancervariants.org/api/v1/ui/#/Associations
    """
    normalized_cancer_type = _normalize_cancer_type(cancer_type)
    search_targets, raw_biomarkers, normalized_genes = _collect_vicc_search_targets(
        required_biomarkers,
        normalized_cancer_type,
    )

    biomarker_annotations: List[dict] = []
    if search_targets:
        with ThreadPoolExecutor(max_workers=min(len(search_targets), 6)) as executor:
            futures = {
                executor.submit(
                    _search_biomarker,
                    target["biomarker"],
                    normalized_cancer_type,
                    search_mode=target["search_mode"],
                    source_biomarker=target["source_biomarker"],
                    parsed=target["parsed"],
                    max_results=max_results_per_biomarker,
                    timeout_seconds=timeout_seconds,
                ): target
                for target in search_targets
            }
            for future in as_completed(futures):
                biomarker_annotations.append(future.result())

        biomarker_annotations.sort(
            key=lambda item: (
                item.get("source_biomarker") or "",
                item.get("search_mode") or "",
                item.get("biomarker") or "",
            )
        )

    matched_drugs = _dedupe_drugs(biomarker_annotations)

    return {
        "data_source": "vicc_metakb_api_v1",
        "api_base": VICC_API_BASE,
        "search_url": VICC_ASSOCIATIONS_URL,
        "cancer_type": normalized_cancer_type,
        "required_biomarkers": raw_biomarkers,
        "required_biomarkers_normalized": normalized_genes,
        "search_count": len(search_targets),
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
