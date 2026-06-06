from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List

from civic_search import search_civic_drugs
from vicc_search import SKIP_DRUG_PATTERN, search_vicc_drugs

DrugRecord = dict


def _normalize_drug_token(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip()).upper()


def _drug_names_from_record(record: DrugRecord) -> List[str]:
    names: List[str] = []
    for name in record.get("drug_names") or []:
        text = str(name or "").strip()
        if text and not SKIP_DRUG_PATTERN.match(text):
            names.append(text)
    if not names:
        for drug in record.get("drugs") or []:
            text = str(drug.get("drug_name") or "").strip()
            if text and not SKIP_DRUG_PATTERN.match(text):
                names.append(text)
    return names


def _expand_records_by_drug_name(records: Iterable[DrugRecord], source: str) -> List[DrugRecord]:
    expanded: List[DrugRecord] = []
    for record in records:
        drug_names = _drug_names_from_record(record)
        if not drug_names:
            continue
        for drug_name in drug_names:
            expanded.append(
                {
                    **record,
                    "drug_name": drug_name,
                    "drug_names": [drug_name],
                    "drugs": [
                        {
                            "drug_name": drug_name,
                            "ncit_code": None,
                            "url": record.get("url"),
                        }
                    ],
                    "data_sources": [source],
                }
            )
    return expanded


def _merge_drug_records(vicc_records: Iterable[DrugRecord], civic_records: Iterable[DrugRecord]) -> List[DrugRecord]:
    merged: dict[str, DrugRecord] = {}

    for record in _expand_records_by_drug_name(vicc_records, "vicc"):
        key = _normalize_drug_token(record["drug_name"])
        if not key:
            continue
        if key not in merged:
            merged[key] = record
            continue

        existing = merged[key]
        existing_sources = set(existing.get("data_sources") or [])
        existing_sources.update(record.get("data_sources") or [])
        existing["data_sources"] = sorted(existing_sources)
        existing["data_source"] = "+".join(existing["data_sources"])
        if not existing.get("url") and record.get("url"):
            existing["url"] = record.get("url")

    for record in _expand_records_by_drug_name(civic_records, "civic"):
        key = _normalize_drug_token(record["drug_name"])
        if not key:
            continue
        if key not in merged:
            merged[key] = record
            continue

        existing = merged[key]
        existing_sources = set(existing.get("data_sources") or [])
        existing_sources.update(record.get("data_sources") or [])
        existing["data_sources"] = sorted(existing_sources)
        existing["data_source"] = "+".join(existing["data_sources"])

        if not existing.get("url") and record.get("url"):
            existing["url"] = record.get("url")
        if not existing.get("publication_url") and record.get("publication_url"):
            existing["publication_url"] = record.get("publication_url")
        if not existing.get("description") and record.get("description"):
            existing["description"] = record.get("description")
        if not existing.get("evidence_label") and record.get("evidence_label"):
            existing["evidence_label"] = record.get("evidence_label")
        if not existing.get("response_type") and record.get("response_type"):
            existing["response_type"] = record.get("response_type")
        if record.get("civic_evidence_id"):
            civic_ids = set(existing.get("civic_evidence_ids") or [])
            civic_ids.add(record["civic_evidence_id"])
            existing["civic_evidence_ids"] = sorted(civic_ids)

    return sorted(
        merged.values(),
        key=lambda item: (
            0 if "civic" in (item.get("data_sources") or []) else 1,
            item.get("evidence_label") or "",
            item.get("drug_name") or "",
        ),
    )


def search_combined_drugs(
    required_biomarkers: Iterable[str],
    cancer_type: str | None = None,
    *,
    max_results_per_biomarker: int = 50,
    timeout_seconds: int = 30,
) -> dict:
    """
    Search VICC Meta-Knowledgebase and CIViC GraphQL for therapies matching
    required biomarkers, returning deduplicated drug records.
    """
    with ThreadPoolExecutor(max_workers=2) as executor:
        vicc_future = executor.submit(
            search_vicc_drugs,
            required_biomarkers,
            cancer_type,
            max_results_per_biomarker=max_results_per_biomarker,
            timeout_seconds=timeout_seconds,
        )
        civic_future = executor.submit(
            search_civic_drugs,
            required_biomarkers,
            cancer_type,
            max_results_per_biomarker=max_results_per_biomarker,
            timeout_seconds=timeout_seconds,
        )
        vicc_result = vicc_future.result()
        civic_result = civic_future.result()

    matched_drugs = _merge_drug_records(
        vicc_result.get("matched_drugs") or [],
        civic_result.get("matched_drugs") or [],
    )

    return {
        "data_source": "vicc_and_civic",
        "cancer_type": vicc_result.get("cancer_type"),
        "required_biomarkers": vicc_result.get("required_biomarkers") or [],
        "required_biomarkers_normalized": vicc_result.get("required_biomarkers_normalized") or [],
        "matched_drug_count": len(matched_drugs),
        "matched_drugs": matched_drugs,
        "vicc": vicc_result,
        "civic": civic_result,
    }


DEMO_DRUG_SEARCH_EXAMPLE = {
    "required_biomarkers": ["KRAS mutation"],
    "cancer_type": "non-small cell lung cancer",
}


if __name__ == "__main__":
    import json

    demo = search_combined_drugs(**DEMO_DRUG_SEARCH_EXAMPLE)
    print(json.dumps(demo, indent=2))
    print(f"matched_drug_count: {demo['matched_drug_count']}")
