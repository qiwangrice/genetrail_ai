from __future__ import annotations

import json
import re
from typing import Dict, Iterable, List, Set

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

from cbioportal_search import DEFAULT_NSCLC_GENES, get_database_url
from vicc_search import _parse_biomarker, _required_variant_tokens

load_dotenv()

PROTEIN_VARIANT_PATTERN = re.compile(r"\b[A-Z][0-9]+[A-Z]?\b")
FUSION_CLASSIFICATION_MARKERS = (
    "FUSION",
    "TRANSLOCATION",
    "REARRANGEMENT",
)


def _normalize_biomarker_list(biomarkers: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    normalized: List[str] = []
    for biomarker in biomarkers:
        text = str(biomarker or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _genes_for_biomarkers(biomarkers: Iterable[str]) -> List[str]:
    genes: Set[str] = set()
    for biomarker in biomarkers:
        parsed = _parse_biomarker(biomarker)
        biomarker_type = parsed.get("type")
        if biomarker_type == "mutation":
            gene = parsed.get("gene")
            if gene:
                genes.add(gene.upper())
        elif biomarker_type == "fusion":
            if parsed.get("gene_a"):
                genes.add(str(parsed["gene_a"]).upper())
            gene_b = parsed.get("gene_b")
            if gene_b and str(gene_b).upper() not in {"FUSION", "FUSIONS", "REARRANGEMENT"}:
                genes.add(str(gene_b).upper())
        elif biomarker_type == "copy_number":
            gene = parsed.get("gene")
            if gene:
                genes.add(gene.upper())
        elif biomarker_type == "gene":
            gene = parsed.get("gene")
            if gene:
                genes.add(gene.upper())
        else:
            gene = biomarker.strip().upper().split()[0]
            if gene:
                genes.add(gene)
    return sorted(genes)


def _protein_change_tokens(protein_change: str | None) -> Set[str]:
    text = str(protein_change or "").strip().upper()
    if not text:
        return set()

    tokens = {text}
    if text.startswith("P."):
        tokens.add(text[2:])
    for match in PROTEIN_VARIANT_PATTERN.finditer(text):
        tokens.add(match.group(0))
    return tokens


def _mutation_matches_biomarker(mutation: dict, biomarker: str) -> bool:
    parsed = _parse_biomarker(biomarker)
    gene = str(mutation.get("gene_symbol") or "").upper()
    protein_change = str(mutation.get("protein_change") or "")
    variant_classification = str(mutation.get("variant_classification") or "").upper()
    biomarker_type = parsed.get("type")

    if biomarker_type == "mutation":
        if gene != str(parsed.get("gene") or "").upper():
            return False
        required_variants = _required_variant_tokens(parsed)
        if not required_variants:
            return True
        protein_tokens = _protein_change_tokens(protein_change)
        return any(variant in protein_tokens for variant in required_variants)

    if biomarker_type == "fusion":
        target_genes = {
            str(parsed.get("gene_a") or "").upper(),
            str(parsed.get("gene_b") or "").upper(),
        } - {""}
        if gene not in target_genes:
            return False
        if any(marker in variant_classification for marker in FUSION_CLASSIFICATION_MARKERS):
            return True
        return True

    if biomarker_type == "copy_number":
        return gene == str(parsed.get("gene") or "").upper()

    if biomarker_type == "gene":
        return gene == str(parsed.get("gene") or "").upper()

    fallback_gene = biomarker.strip().upper().split()[0]
    return bool(fallback_gene) and gene == fallback_gene


def _model_matches_all_biomarkers(
    mutations: List[dict],
    biomarkers: Iterable[str],
) -> bool:
    biomarker_list = _normalize_biomarker_list(biomarkers)
    if not biomarker_list:
        return True
    return all(
        any(_mutation_matches_biomarker(mutation, biomarker) for mutation in mutations)
        for biomarker in biomarker_list
    )


def _model_matches_any_biomarker(
    mutations: List[dict],
    biomarkers: Iterable[str],
) -> bool:
    biomarker_list = _normalize_biomarker_list(biomarkers)
    if not biomarker_list:
        return False
    return any(
        any(_mutation_matches_biomarker(mutation, biomarker) for mutation in mutations)
        for biomarker in biomarker_list
    )


def _eligible_model_ids(
    model_ids: Set[str],
    mutations_by_model: Dict[str, List[dict]],
    required: Iterable[str],
    excluded: Iterable[str],
) -> List[str]:
    eligible: List[str] = []
    for model_id in sorted(model_ids):
        mutations = mutations_by_model.get(model_id, [])
        has_required = _model_matches_all_biomarkers(mutations, required)
        has_excluded = _model_matches_any_biomarker(mutations, excluded)
        if has_required and not has_excluded:
            eligible.append(model_id)
    return eligible


def _ensure_depmap_tables(cur) -> None:
    cur.execute(
        """
        SELECT COUNT(*) AS table_count
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN (
              'depmap_models',
              'depmap_mutations',
              'depmap_gene_effect',
              'depmap_prism_treatments',
              'depmap_drug_sensitivity'
          )
        """
    )
    if cur.fetchone()["table_count"] < 5:
        raise RuntimeError(
            "Neon Postgres is missing DepMap tables. Run:\n"
            "  poetry run python database/load_depmap_to_neon.py --truncate"
        )


def _fetch_mutations_by_model(cur, gene_filter: List[str]) -> Dict[str, List[dict]]:
    cur.execute(
        """
        SELECT model_id, gene_symbol, protein_change, variant_classification, variant_type
        FROM depmap_mutations
        WHERE gene_symbol = ANY(%s)
        ORDER BY model_id, gene_symbol, protein_change
        """,
        (gene_filter,),
    )
    mutations_by_model: Dict[str, List[dict]] = {}
    for row in cur.fetchall():
        model_id = row["model_id"]
        mutations_by_model.setdefault(model_id, []).append(dict(row))
    return mutations_by_model


def _fetch_gene_cell_line_counts(cur, gene_filter: List[str], total_models: int) -> List[dict]:
    cur.execute(
        """
        SELECT gene_symbol,
               COUNT(DISTINCT model_id) AS cell_lines_with_mutation
        FROM depmap_mutations
        WHERE gene_symbol = ANY(%s)
        GROUP BY gene_symbol
        ORDER BY gene_symbol
        """,
        (gene_filter,),
    )
    return [
        {
            "gene_symbol": row["gene_symbol"],
            "cell_lines_with_mutation": row["cell_lines_with_mutation"],
            "percentage_of_models": round(
                100 * row["cell_lines_with_mutation"] / max(total_models, 1),
                1,
            ),
        }
        for row in cur.fetchall()
    ]


def _fetch_models(cur, model_ids: List[str]) -> Dict[str, dict]:
    if not model_ids:
        return {}
    cur.execute(
        """
        SELECT model_id,
               cell_line_name,
               stripped_cell_line_name,
               oncotree_lineage,
               oncotree_primary_disease,
               oncotree_subtype,
               oncotree_code,
               depmap_release
        FROM depmap_models
        WHERE model_id = ANY(%s)
        ORDER BY model_id
        """,
        (model_ids,),
    )
    return {row["model_id"]: dict(row) for row in cur.fetchall()}


def _fetch_gene_effects(cur, model_ids: List[str], genes: List[str]) -> Dict[str, Dict[str, float | None]]:
    if not model_ids:
        return {}
    cur.execute(
        """
        SELECT model_id, gene_symbol, gene_effect
        FROM depmap_gene_effect
        WHERE model_id = ANY(%s)
          AND gene_symbol = ANY(%s)
        ORDER BY model_id, gene_symbol
        """,
        (model_ids, genes),
    )
    effects_by_model: Dict[str, Dict[str, float | None]] = {}
    for row in cur.fetchall():
        effects_by_model.setdefault(row["model_id"], {})[row["gene_symbol"]] = row["gene_effect"]
    return effects_by_model


def _matching_mutations_for_model(
    mutations: List[dict],
    required: Iterable[str],
    excluded: Iterable[str],
) -> List[dict]:
    biomarkers = _normalize_biomarker_list(required) + _normalize_biomarker_list(excluded)
    matched: List[dict] = []
    seen: Set[tuple[str, str, str]] = set()
    for biomarker in biomarkers:
        for mutation in mutations:
            if not _mutation_matches_biomarker(mutation, biomarker):
                continue
            key = (
                mutation["gene_symbol"],
                mutation.get("protein_change") or "",
                mutation.get("variant_classification") or "",
            )
            if key in seen:
                continue
            seen.add(key)
            matched.append(dict(mutation))
    return matched


def _summarize_gene_effects(
    effects_by_model: Dict[str, Dict[str, float | None]],
    genes: List[str],
) -> List[dict]:
    summaries: List[dict] = []
    for gene in genes:
        values = [
            effect[gene]
            for effect in effects_by_model.values()
            if effect.get(gene) is not None
        ]
        if not values:
            continue
        summaries.append(
            {
                "gene_symbol": gene,
                "model_count": len(values),
                "mean_gene_effect": round(sum(values) / len(values), 3),
                "min_gene_effect": round(min(values), 3),
                "max_gene_effect": round(max(values), 3),
            }
        )
    return summaries


def _fetch_drug_sensitivity_summary(cur, model_ids: List[str], limit: int = 20) -> List[dict]:
    if not model_ids:
        return []
    cur.execute(
        """
        SELECT t.drug_name,
               t.broad_id,
               t.moa,
               t.target,
               s.screen_type,
               COUNT(DISTINCT s.model_id) AS sensitive_model_count,
               ROUND(AVG(s.log_fold_change)::numeric, 3) AS mean_log_fold_change,
               ROUND(MIN(s.log_fold_change)::numeric, 3) AS best_log_fold_change
        FROM depmap_drug_sensitivity s
        JOIN depmap_prism_treatments t
          ON s.screen_type = t.screen_type
         AND s.column_name = t.column_name
        WHERE s.model_id = ANY(%s)
        GROUP BY t.drug_name, t.broad_id, t.moa, t.target, s.screen_type
        ORDER BY mean_log_fold_change ASC, sensitive_model_count DESC
        LIMIT %s
        """,
        (model_ids, limit),
    )
    return [
        {
            **dict(row),
            "mean_log_fold_change": float(row["mean_log_fold_change"])
            if row["mean_log_fold_change"] is not None
            else None,
            "best_log_fold_change": float(row["best_log_fold_change"])
            if row["best_log_fold_change"] is not None
            else None,
        }
        for row in cur.fetchall()
    ]


def _build_model_records(
    eligible_model_ids: List[str],
    models_by_id: Dict[str, dict],
    mutations_by_model: Dict[str, List[dict]],
    effects_by_model: Dict[str, Dict[str, float | None]],
    required: Iterable[str],
    excluded: Iterable[str],
) -> List[dict]:
    records: List[dict] = []
    for model_id in eligible_model_ids:
        model = models_by_id.get(model_id, {"model_id": model_id})
        records.append(
            {
                **model,
                "matching_mutations": _matching_mutations_for_model(
                    mutations_by_model.get(model_id, []),
                    required,
                    excluded,
                ),
                "gene_effects": effects_by_model.get(model_id, {}),
            }
        )
    return records


def search_depmap_for_cell_lines(
    result,
    *,
    genes: Iterable[str] | None = None,
    limit: int | None = 25,
    include_drug_sensitivity: bool = True,
    drug_summary_limit: int = 20,
) -> dict:
    """
    Search DepMap lung/NSCLC cell lines in Neon Postgres using trial eligibility.

    A model is eligible when it matches all required_biomarkers and none of the
    excluded_biomarkers, using depmap_mutations (gene + variant when specified).
    """
    required = _normalize_biomarker_list(result.required_biomarkers or [])
    excluded = _normalize_biomarker_list(result.excluded_biomarkers or [])
    gene_filter = sorted(
        set(genes or DEFAULT_NSCLC_GENES)
        | set(_genes_for_biomarkers(required))
        | set(_genes_for_biomarkers(excluded))
    )

    conn = psycopg2.connect(get_database_url())
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _ensure_depmap_tables(cur)

            cur.execute("SELECT COUNT(*) AS count FROM depmap_models")
            total_models = cur.fetchone()["count"]

            cur.execute("SELECT model_id FROM depmap_models ORDER BY model_id")
            all_model_ids = {row["model_id"] for row in cur.fetchall()}

            mutations_by_model = _fetch_mutations_by_model(cur, gene_filter)
            gene_cell_line_counts = _fetch_gene_cell_line_counts(
                cur,
                gene_filter,
                total_models,
            )

            models_with_required = 0
            for model_id in all_model_ids:
                mutations = mutations_by_model.get(model_id, [])
                if _model_matches_all_biomarkers(mutations, required):
                    models_with_required += 1

            eligible_model_ids = _eligible_model_ids(
                all_model_ids,
                mutations_by_model,
                required,
                excluded,
            )
            eligible_count = len(eligible_model_ids)

            display_model_ids = eligible_model_ids
            if limit is not None:
                display_model_ids = eligible_model_ids[: max(limit, 0)]

            models_by_id = _fetch_models(cur, display_model_ids)
            effects_for_eligible = _fetch_gene_effects(cur, eligible_model_ids, gene_filter)
            effects_by_model = {
                model_id: effects_for_eligible.get(model_id, {})
                for model_id in display_model_ids
            }
            models = _build_model_records(
                display_model_ids,
                models_by_id,
                mutations_by_model,
                effects_by_model,
                required,
                excluded,
            )

            gene_effect_summary = _summarize_gene_effects(effects_for_eligible, gene_filter)
            drug_sensitivity_summary = (
                _fetch_drug_sensitivity_summary(cur, eligible_model_ids, drug_summary_limit)
                if include_drug_sensitivity
                else []
            )
    finally:
        conn.close()

    return {
        "cancer_type": getattr(result, "cancer_type", None),
        "data_source": "neon_postgres_depmap",
        "genes_queried": gene_filter,
        "required_biomarkers": required,
        "excluded_biomarkers": excluded,
        "total_cell_lines": total_models,
        "cell_lines_with_required_biomarkers": models_with_required,
        "cell_lines_without_required_biomarkers": total_models - models_with_required,
        "eligible_cell_lines": eligible_count,
        "gene_cell_line_counts": gene_cell_line_counts,
        "gene_effect_summary": gene_effect_summary,
        "drug_sensitivity_summary": drug_sensitivity_summary,
        "models_returned": len(models),
        "models": models,
    }


DEMO_DEPMAP_EXAMPLE = {
    "cancer_type": "metastatic non-small cell lung cancer",
    "required_biomarkers": ["KRAS G12C mutation"],
    "excluded_biomarkers": ["EGFR activating mutations", "ALK fusions"],
}


if __name__ == "__main__":
    from types import SimpleNamespace

    demo = SimpleNamespace(**DEMO_DEPMAP_EXAMPLE)
    payload = search_depmap_for_cell_lines(demo, limit=5)
    print(json.dumps(payload, indent=2))
    print(f"eligible_cell_lines: {payload['eligible_cell_lines']}")
