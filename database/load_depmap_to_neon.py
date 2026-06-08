from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cbioportal_search import DEFAULT_NSCLC_GENES, get_database_url

load_dotenv(ROOT / ".env")

NSCLC_TYPE_KEYWORDS = {
    "nsclc",
    "luad",
    "lusc",
    "lung",
    "pluad",
    "pluc",
    "nosclc",
    "nsccl",
    "lclc",
    "nutcl",
}

GENE_HEADER_PATTERN = re.compile(r"^(.+?) \((\d+)\)$")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS depmap_models (
    model_id TEXT PRIMARY KEY,
    cell_line_name TEXT,
    stripped_cell_line_name TEXT,
    oncotree_lineage TEXT,
    oncotree_primary_disease TEXT,
    oncotree_subtype TEXT,
    oncotree_code TEXT,
    depmap_release TEXT
);

CREATE TABLE IF NOT EXISTS depmap_mutations (
    model_id TEXT NOT NULL,
    gene_symbol TEXT NOT NULL,
    protein_change TEXT NOT NULL DEFAULT '',
    variant_classification TEXT,
    variant_type TEXT,
    PRIMARY KEY (model_id, gene_symbol, protein_change)
);

CREATE TABLE IF NOT EXISTS depmap_gene_effect (
    model_id TEXT NOT NULL,
    gene_symbol TEXT NOT NULL,
    gene_effect DOUBLE PRECISION,
    PRIMARY KEY (model_id, gene_symbol)
);

CREATE INDEX IF NOT EXISTS idx_depmap_mutations_gene
    ON depmap_mutations(gene_symbol);
CREATE INDEX IF NOT EXISTS idx_depmap_mutations_model
    ON depmap_mutations(model_id);
CREATE INDEX IF NOT EXISTS idx_depmap_gene_effect_gene
    ON depmap_gene_effect(gene_symbol);
CREATE INDEX IF NOT EXISTS idx_depmap_gene_effect_model
    ON depmap_gene_effect(model_id);

CREATE TABLE IF NOT EXISTS depmap_prism_treatments (
    screen_type TEXT NOT NULL,
    column_name TEXT NOT NULL,
    broad_id TEXT,
    drug_name TEXT,
    dose_um DOUBLE PRECISION,
    screen_id TEXT,
    compound_plate TEXT,
    moa TEXT,
    target TEXT,
    disease_area TEXT,
    indication TEXT,
    phase TEXT,
    PRIMARY KEY (screen_type, column_name)
);

CREATE TABLE IF NOT EXISTS depmap_drug_sensitivity (
    model_id TEXT NOT NULL,
    screen_type TEXT NOT NULL,
    column_name TEXT NOT NULL,
    log_fold_change DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (model_id, screen_type, column_name)
);

CREATE INDEX IF NOT EXISTS idx_depmap_prism_treatments_drug_name
    ON depmap_prism_treatments(drug_name);
CREATE INDEX IF NOT EXISTS idx_depmap_prism_treatments_broad_id
    ON depmap_prism_treatments(broad_id);
CREATE INDEX IF NOT EXISTS idx_depmap_drug_sensitivity_model
    ON depmap_drug_sensitivity(model_id);
CREATE INDEX IF NOT EXISTS idx_depmap_drug_sensitivity_screen
    ON depmap_drug_sensitivity(screen_type);
"""

ModelRow = Tuple[
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
]
MutationRow = Tuple[str, str, str, str | None, str | None]
GeneEffectRow = Tuple[str, str, float | None]
TreatmentRow = Tuple[
    str,
    str,
    str | None,
    str | None,
    float | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
]
SensitivityRow = Tuple[str, str, str, float]


def _normalize_gene_symbol(value: str | None) -> str:
    return str(value or "").strip().upper()


def _is_lung_nsclc_model_row(row: dict[str, str]) -> bool:
    lineage = str(row.get("OncotreeLineage") or "").strip().lower()
    if lineage != "lung":
        return False

    primary = str(row.get("OncotreePrimaryDisease") or "").strip().lower()
    subtype = str(row.get("OncotreeSubtype") or "").strip().lower()
    code = str(row.get("OncotreeCode") or "").strip().lower()
    haystack = " ".join([primary, subtype, code])

    if "non-cancerous" in primary or "immortalized lung" in subtype:
        return False
    if "small cell" in haystack and "non-small cell" not in haystack:
        return False

    return any(keyword in haystack for keyword in NSCLC_TYPE_KEYWORDS) or primary.startswith(
        "non-small cell lung"
    )


def load_lung_models(model_csv: Path, release: str) -> Tuple[List[ModelRow], Set[str]]:
    models: List[ModelRow] = []
    model_ids: Set[str] = set()

    with model_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not _is_lung_nsclc_model_row(row):
                continue

            model_id = str(row.get("ModelID") or "").strip()
            if not model_id:
                continue

            models.append(
                (
                    model_id,
                    row.get("CellLineName"),
                    row.get("StrippedCellLineName"),
                    row.get("OncotreeLineage"),
                    row.get("OncotreePrimaryDisease"),
                    row.get("OncotreeSubtype"),
                    row.get("OncotreeCode"),
                    release or None,
                )
            )
            model_ids.add(model_id)

    models.sort(key=lambda item: item[0])
    return models, model_ids


def _parse_gene_header(column_name: str) -> str | None:
    text = str(column_name or "").strip()
    if not text:
        return None

    match = GENE_HEADER_PATTERN.match(text)
    symbol = match.group(1) if match else text
    return _normalize_gene_symbol(symbol)


def _build_gene_column_indices(
    header: Sequence[str],
    target_genes: Set[str],
) -> Dict[str, int]:
    indices: Dict[str, int] = {}
    for index, column_name in enumerate(header):
        symbol = _parse_gene_header(column_name)
        if symbol and symbol in target_genes:
            indices[symbol] = index
    return indices


def _parse_gene_effect(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text or text.upper() == "NA":
        return None
    return text


def _optional_float(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text or text.upper() == "NA":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_prism_treatment_info(
    treatment_info_csv: Path,
    screen_type: str,
) -> List[TreatmentRow]:
    treatments: List[TreatmentRow] = []

    with treatment_info_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            column_name = str(row.get("column_name") or "").strip()
            if not column_name:
                continue

            treatments.append(
                (
                    screen_type,
                    column_name,
                    _optional_text(row.get("broad_id")),
                    _optional_text(row.get("name")),
                    _optional_float(row.get("dose")),
                    _optional_text(row.get("screen_id")),
                    _optional_text(row.get("compound_plate")),
                    _optional_text(row.get("moa")),
                    _optional_text(row.get("target")),
                    _optional_text(row.get("disease.area")),
                    _optional_text(row.get("indication")),
                    _optional_text(row.get("phase")),
                )
            )

    treatments.sort(key=lambda item: item[1])
    print(f"Loaded {len(treatments)} {screen_type} PRISM treatment definitions")
    return treatments


def _build_lfc_column_map(
    header: Sequence[str],
    treatment_columns: Set[str],
) -> List[Tuple[int, str]]:
    mapped: List[Tuple[int, str]] = []
    for index, column_name in enumerate(header):
        if index == 0 or not column_name:
            continue
        if column_name in treatment_columns:
            mapped.append((index, column_name))
    return mapped


def load_prism_logfold_change(
    logfold_change_csv: Path,
    screen_type: str,
    model_ids: Set[str],
    treatment_columns: Set[str],
    broad_id_by_column: Dict[str, str | None],
    *,
    batch_size: int,
    min_effect: float | None = None,
    collapse_doses_by_broad_id: bool = False,
) -> Iterable[List[SensitivityRow]]:
    batch: List[SensitivityRow] = []
    models_seen = 0
    values_seen = 0
    best_by_model_drug: Dict[Tuple[str, str], SensitivityRow] = {}

    def consider(row: SensitivityRow) -> Iterable[List[SensitivityRow]]:
        nonlocal batch, values_seen
        if min_effect is not None and row[3] > min_effect:
            return
        if collapse_doses_by_broad_id:
            broad_id = broad_id_by_column.get(row[2]) or row[2]
            key = (row[0], broad_id)
            existing = best_by_model_drug.get(key)
            if existing is None or row[3] < existing[3]:
                best_by_model_drug[key] = row
            return

        batch.append(row)
        values_seen += 1
        if len(batch) >= batch_size:
            yield batch
            batch.clear()

    with logfold_change_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        column_map = _build_lfc_column_map(header, treatment_columns)
        if not column_map:
            raise RuntimeError(
                f"No overlapping columns between {logfold_change_csv.name} and treatment info"
            )

        missing_columns = treatment_columns - {name for _, name in column_map}
        if missing_columns:
            print(
                f"Warning: {len(missing_columns)} {screen_type} treatment columns "
                "missing from log-fold-change matrix"
            )

        for row in reader:
            if not row:
                continue
            model_id = str(row[0]).strip()
            if model_id not in model_ids:
                continue

            models_seen += 1
            for column_index, column_name in column_map:
                if column_index >= len(row):
                    continue
                log_fold_change = _parse_gene_effect(row[column_index])
                if log_fold_change is None:
                    continue

                yield from consider((model_id, screen_type, column_name, log_fold_change))

    if collapse_doses_by_broad_id:
        collapsed_rows = list(best_by_model_drug.values())
        values_seen = len(collapsed_rows)
        for index in range(0, len(collapsed_rows), batch_size):
            yield collapsed_rows[index : index + batch_size]
    elif batch:
        yield batch

    collapse_note = " (best dose per drug)" if collapse_doses_by_broad_id else ""
    print(
        f"Processed {screen_type} PRISM log-fold-change for {models_seen} lung models "
        f"({values_seen} sensitivity values{collapse_note})"
    )


def load_gene_effects(
    gene_effect_csv: Path,
    model_ids: Set[str],
    target_genes: Set[str],
    *,
    batch_size: int,
) -> Iterable[List[GeneEffectRow]]:
    batch: List[GeneEffectRow] = []
    rows_seen = 0

    with gene_effect_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        gene_indices = _build_gene_column_indices(header, target_genes)
        missing_genes = sorted(target_genes - set(gene_indices))
        if missing_genes:
            print(f"Warning: genes missing from CRISPRGeneEffect header: {', '.join(missing_genes)}")

        for row in reader:
            if not row:
                continue
            model_id = str(row[0]).strip()
            if model_id not in model_ids:
                continue

            rows_seen += 1
            for gene_symbol, column_index in gene_indices.items():
                if column_index >= len(row):
                    continue
                batch.append(
                    (
                        model_id,
                        gene_symbol,
                        _parse_gene_effect(row[column_index]),
                    )
                )
                if len(batch) >= batch_size:
                    yield batch
                    batch = []

    if batch:
        yield batch

    print(f"Processed CRISPR gene effect rows for {rows_seen} lung models")


def load_mutations(
    maf_path: Path,
    model_ids: Set[str],
    target_genes: Set[str],
    *,
    default_only: bool,
    batch_size: int,
) -> Iterable[List[MutationRow]]:
    batch: List[MutationRow] = []
    rows_seen = 0

    with maf_path.open("r", encoding="utf-8", newline="") as handle:
        header_line = handle.readline().rstrip("\n")
        if not header_line:
            return

        header = header_line.split("\t")
        column_index = {name: index for index, name in enumerate(header)}
        required = ["ModelID", "Hugo_Symbol", "Protein_Change", "Variant_Classification", "Variant_Type"]
        missing_columns = [name for name in required if name not in column_index]
        if missing_columns:
            raise RuntimeError(
                f"MAF file missing required columns: {', '.join(missing_columns)}"
            )

        default_index = column_index.get("IsDefaultEntryForModel")

        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < len(header):
                continue

            model_id = parts[column_index["ModelID"]].strip()
            if model_id not in model_ids:
                continue

            if default_only and default_index is not None:
                if parts[default_index].strip().lower() != "yes":
                    continue

            gene_symbol = _normalize_gene_symbol(parts[column_index["Hugo_Symbol"]])
            if gene_symbol not in target_genes:
                continue

            protein_change = str(parts[column_index["Protein_Change"]] or "").strip()
            batch.append(
                (
                    model_id,
                    gene_symbol,
                    protein_change,
                    parts[column_index["Variant_Classification"]].strip() or None,
                    parts[column_index["Variant_Type"]].strip() or None,
                )
            )
            rows_seen += 1
            if len(batch) >= batch_size:
                yield batch
                batch = []

    if batch:
        yield batch

    print(f"Loaded {rows_seen} mutation rows from MAF")


def _truncate_tables(cur) -> None:
    cur.execute(
        "TRUNCATE depmap_drug_sensitivity, depmap_prism_treatments, "
        "depmap_gene_effect, depmap_mutations, depmap_models"
    )


def _insert_models(cur, models: List[ModelRow]) -> None:
    execute_values(
        cur,
        """
        INSERT INTO depmap_models (
            model_id,
            cell_line_name,
            stripped_cell_line_name,
            oncotree_lineage,
            oncotree_primary_disease,
            oncotree_subtype,
            oncotree_code,
            depmap_release
        ) VALUES %s
        ON CONFLICT (model_id) DO UPDATE SET
            cell_line_name = EXCLUDED.cell_line_name,
            stripped_cell_line_name = EXCLUDED.stripped_cell_line_name,
            oncotree_lineage = EXCLUDED.oncotree_lineage,
            oncotree_primary_disease = EXCLUDED.oncotree_primary_disease,
            oncotree_subtype = EXCLUDED.oncotree_subtype,
            oncotree_code = EXCLUDED.oncotree_code,
            depmap_release = EXCLUDED.depmap_release
        """,
        models,
        page_size=500,
    )


def _insert_mutations(cur, rows: List[MutationRow]) -> None:
    execute_values(
        cur,
        """
        INSERT INTO depmap_mutations (
            model_id,
            gene_symbol,
            protein_change,
            variant_classification,
            variant_type
        ) VALUES %s
        ON CONFLICT (model_id, gene_symbol, protein_change) DO UPDATE SET
            variant_classification = EXCLUDED.variant_classification,
            variant_type = EXCLUDED.variant_type
        """,
        rows,
        page_size=1000,
    )


def _insert_gene_effects(cur, rows: List[GeneEffectRow]) -> None:
    execute_values(
        cur,
        """
        INSERT INTO depmap_gene_effect (
            model_id,
            gene_symbol,
            gene_effect
        ) VALUES %s
        ON CONFLICT (model_id, gene_symbol) DO UPDATE SET
            gene_effect = EXCLUDED.gene_effect
        """,
        rows,
        page_size=1000,
    )


def _insert_prism_treatments(cur, rows: List[TreatmentRow]) -> None:
    execute_values(
        cur,
        """
        INSERT INTO depmap_prism_treatments (
            screen_type,
            column_name,
            broad_id,
            drug_name,
            dose_um,
            screen_id,
            compound_plate,
            moa,
            target,
            disease_area,
            indication,
            phase
        ) VALUES %s
        ON CONFLICT (screen_type, column_name) DO UPDATE SET
            broad_id = EXCLUDED.broad_id,
            drug_name = EXCLUDED.drug_name,
            dose_um = EXCLUDED.dose_um,
            screen_id = EXCLUDED.screen_id,
            compound_plate = EXCLUDED.compound_plate,
            moa = EXCLUDED.moa,
            target = EXCLUDED.target,
            disease_area = EXCLUDED.disease_area,
            indication = EXCLUDED.indication,
            phase = EXCLUDED.phase
        """,
        rows,
        page_size=1000,
    )


def _insert_drug_sensitivity(cur, rows: List[SensitivityRow]) -> None:
    execute_values(
        cur,
        """
        INSERT INTO depmap_drug_sensitivity (
            model_id,
            screen_type,
            column_name,
            log_fold_change
        ) VALUES %s
        ON CONFLICT (model_id, screen_type, column_name) DO UPDATE SET
            log_fold_change = EXCLUDED.log_fold_change
        """,
        rows,
        page_size=2000,
    )


def load_prism_drug_sensitivity_to_neon(
    *,
    model_ids: Set[str],
    primary_lfc_csv: Path | None,
    primary_treatment_info_csv: Path | None,
    secondary_lfc_csv: Path | None,
    secondary_treatment_info_csv: Path | None,
    batch_size: int,
    min_effect: float | None,
    collapse_secondary_doses: bool,
    cur,
) -> dict[str, int]:
    treatment_count = 0
    sensitivity_count = 0

    screen_configs = [
        ("primary", primary_lfc_csv, primary_treatment_info_csv, False),
        ("secondary", secondary_lfc_csv, secondary_treatment_info_csv, collapse_secondary_doses),
    ]

    for screen_type, lfc_csv, treatment_csv, collapse_doses in screen_configs:
        if not lfc_csv and not treatment_csv:
            continue
        if not lfc_csv or not treatment_csv:
            raise RuntimeError(
                f"Both log-fold-change and treatment-info files are required for {screen_type} screen"
            )
        if not lfc_csv.exists():
            raise RuntimeError(f"{screen_type} log-fold-change file not found: {lfc_csv}")
        if not treatment_csv.exists():
            raise RuntimeError(f"{screen_type} treatment-info file not found: {treatment_csv}")

        print(f"Loading {screen_type} PRISM treatment metadata...")
        treatments = load_prism_treatment_info(treatment_csv, screen_type)
        _insert_prism_treatments(cur, treatments)
        treatment_count += len(treatments)

        treatment_columns = {item[1] for item in treatments}
        broad_id_by_column = {item[1]: item[2] for item in treatments}
        print(f"Loading {screen_type} PRISM drug sensitivity values...")
        for batch in load_prism_logfold_change(
            lfc_csv,
            screen_type,
            model_ids,
            treatment_columns,
            broad_id_by_column,
            batch_size=batch_size,
            min_effect=min_effect,
            collapse_doses_by_broad_id=collapse_doses,
        ):
            _insert_drug_sensitivity(cur, batch)
            sensitivity_count += len(batch)

    return {
        "prism_treatments": treatment_count,
        "drug_sensitivity_values": sensitivity_count,
    }


def load_depmap_to_neon(
    *,
    model_csv: Path,
    maf_path: Path | None,
    gene_effect_csv: Path | None,
    primary_lfc_csv: Path | None = None,
    primary_treatment_info_csv: Path | None = None,
    secondary_lfc_csv: Path | None = None,
    secondary_treatment_info_csv: Path | None = None,
    release: str,
    truncate: bool,
    default_mutations_only: bool,
    batch_size: int,
    extra_genes: Sequence[str] | None = None,
    skip_mutations: bool = False,
    skip_gene_effects: bool = False,
    skip_prism: bool = False,
    prism_min_effect: float | None = -0.5,
    collapse_secondary_doses: bool = True,
) -> dict[str, int]:
    target_genes = set(DEFAULT_NSCLC_GENES)
    if extra_genes:
        target_genes.update(_normalize_gene_symbol(gene) for gene in extra_genes if gene)

    started = time.time()
    models, model_ids = load_lung_models(model_csv, release)
    if not models:
        raise RuntimeError("No lung/NSCLC DepMap models matched the filter.")

    print(f"Selected {len(models)} lung/NSCLC models")

    conn = psycopg2.connect(get_database_url())
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
                if truncate:
                    print("Truncating existing DepMap tables...")
                    _truncate_tables(cur)

                print("Inserting depmap_models...")
                _insert_models(cur, models)

                mutation_count = 0
                if not skip_mutations:
                    if not maf_path:
                        raise RuntimeError("MAF path is required unless --skip-mutations is set")
                    print("Loading depmap_mutations from MAF...")
                    for batch in load_mutations(
                        maf_path,
                        model_ids,
                        target_genes,
                        default_only=default_mutations_only,
                        batch_size=batch_size,
                    ):
                        _insert_mutations(cur, batch)
                        mutation_count += len(batch)

                effect_count = 0
                if not skip_gene_effects:
                    if not gene_effect_csv:
                        raise RuntimeError(
                            "CRISPRGeneEffect path is required unless --skip-gene-effects is set"
                        )
                    print("Loading depmap_gene_effect from CRISPRGeneEffect...")
                    for batch in load_gene_effects(
                        gene_effect_csv,
                        model_ids,
                        target_genes,
                        batch_size=batch_size,
                    ):
                        _insert_gene_effects(cur, batch)
                        effect_count += len(batch)

                prism_summary = {"prism_treatments": 0, "drug_sensitivity_values": 0}
                if not skip_prism:
                    prism_summary = load_prism_drug_sensitivity_to_neon(
                        model_ids=model_ids,
                        primary_lfc_csv=primary_lfc_csv,
                        primary_treatment_info_csv=primary_treatment_info_csv,
                        secondary_lfc_csv=secondary_lfc_csv,
                        secondary_treatment_info_csv=secondary_treatment_info_csv,
                        batch_size=batch_size,
                        min_effect=prism_min_effect,
                        collapse_secondary_doses=collapse_secondary_doses,
                        cur=cur,
                    )

        elapsed = time.time() - started
        summary = {
            "models": len(models),
            "mutations": mutation_count,
            "gene_effects": effect_count,
            "genes": len(target_genes),
            **prism_summary,
        }
        print(
            "DepMap load complete in "
            f"{elapsed:.1f}s: {summary['models']} models, "
            f"{summary['mutations']} mutations, "
            f"{summary['gene_effects']} gene-effect values, "
            f"{summary['prism_treatments']} PRISM treatments, "
            f"{summary['drug_sensitivity_values']} drug sensitivity values "
            f"across {summary['genes']} genes"
        )
        return summary
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Load DepMap model metadata, lung/NSCLC mutations, CRISPR gene effects, "
            "and PRISM drug sensitivity into Neon Postgres."
        )
    )
    parser.add_argument(
        "--model-csv",
        type=Path,
        default=Path("/Users/qiwang/Downloads/Model.csv"),
        help="Path to DepMap Model.csv",
    )
    parser.add_argument(
        "--maf",
        type=Path,
        default=Path("/Users/qiwang/Downloads/OmicsSomaticMutationsMAF.maf"),
        help="Path to DepMap OmicsSomaticMutationsMAF.maf",
    )
    parser.add_argument(
        "--gene-effect-csv",
        type=Path,
        default=Path("/Users/qiwang/Downloads/CRISPRGeneEffect.csv"),
        help="Path to DepMap CRISPRGeneEffect.csv",
    )
    parser.add_argument(
        "--primary-lfc-csv",
        type=Path,
        default=Path("/Users/qiwang/Downloads/primary-screen-replicate-collapsed-logfold-change.csv"),
        help="Path to primary PRISM replicate-collapsed log-fold-change matrix",
    )
    parser.add_argument(
        "--primary-treatment-info-csv",
        type=Path,
        default=Path("/Users/qiwang/Downloads/primary-screen-replicate-collapsed-treatment-info.csv"),
        help="Path to primary PRISM replicate-collapsed treatment info",
    )
    parser.add_argument(
        "--secondary-lfc-csv",
        type=Path,
        default=Path("/Users/qiwang/Downloads/secondary-screen-replicate-collapsed-logfold-change.csv"),
        help="Path to secondary PRISM replicate-collapsed log-fold-change matrix",
    )
    parser.add_argument(
        "--secondary-treatment-info-csv",
        type=Path,
        default=Path("/Users/qiwang/Downloads/secondary-screen-replicate-collapsed-treatment-info.csv"),
        help="Path to secondary PRISM replicate-collapsed treatment info",
    )
    parser.add_argument(
        "--release",
        default="DepMap local import",
        help="Release label stored in depmap_models.depmap_release",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Clear DepMap tables before loading",
    )
    parser.add_argument(
        "--all-maf-entries",
        action="store_true",
        help="Load all MAF rows instead of IsDefaultEntryForModel=Yes only",
    )
    parser.add_argument(
        "--skip-mutations",
        action="store_true",
        help="Skip loading OmicsSomaticMutationsMAF.maf",
    )
    parser.add_argument(
        "--skip-gene-effects",
        action="store_true",
        help="Skip loading CRISPRGeneEffect.csv",
    )
    parser.add_argument(
        "--skip-prism",
        action="store_true",
        help="Skip loading PRISM primary/secondary drug sensitivity files",
    )
    parser.add_argument(
        "--prism-min-effect",
        type=float,
        default=-0.5,
        help=(
            "Only store sensitivity values with log-fold-change <= this threshold "
            "(default: -0.5; use a large value like 0 to store all values)"
        ),
    )
    parser.add_argument(
        "--secondary-keep-all-doses",
        action="store_true",
        help="Store every secondary-screen dose point (uses much more database space)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2000,
        help="Insert batch size",
    )
    parser.add_argument(
        "--extra-genes",
        default="",
        help="Comma-separated extra gene symbols to include beyond DEFAULT_NSCLC_GENES",
    )
    args = parser.parse_args()

    for path_arg, label in (
        (args.model_csv, "Model.csv"),
    ):
        if not path_arg.exists():
            raise SystemExit(f"{label} not found: {path_arg}")

    if not args.skip_mutations and not args.maf.exists():
        raise SystemExit(f"OmicsSomaticMutationsMAF.maf not found: {args.maf}")
    if not args.skip_gene_effects and not args.gene_effect_csv.exists():
        raise SystemExit(f"CRISPRGeneEffect.csv not found: {args.gene_effect_csv}")

    if not args.skip_prism:
        for path_arg, label in (
            (args.primary_lfc_csv, "primary-screen-replicate-collapsed-logfold-change.csv"),
            (args.primary_treatment_info_csv, "primary-screen-replicate-collapsed-treatment-info.csv"),
            (args.secondary_lfc_csv, "secondary-screen-replicate-collapsed-logfold-change.csv"),
            (args.secondary_treatment_info_csv, "secondary-screen-replicate-collapsed-treatment-info.csv"),
        ):
            if not path_arg.exists():
                raise SystemExit(f"{label} not found: {path_arg}")

    extra_genes = [
        gene.strip()
        for gene in args.extra_genes.split(",")
        if gene.strip()
    ]

    load_depmap_to_neon(
        model_csv=args.model_csv,
        maf_path=args.maf,
        gene_effect_csv=args.gene_effect_csv,
        primary_lfc_csv=args.primary_lfc_csv,
        primary_treatment_info_csv=args.primary_treatment_info_csv,
        secondary_lfc_csv=args.secondary_lfc_csv,
        secondary_treatment_info_csv=args.secondary_treatment_info_csv,
        release=args.release,
        truncate=args.truncate,
        default_mutations_only=not args.all_maf_entries,
        batch_size=max(args.batch_size, 100),
        extra_genes=extra_genes,
        skip_mutations=args.skip_mutations,
        skip_gene_effects=args.skip_gene_effects,
        skip_prism=args.skip_prism,
        prism_min_effect=args.prism_min_effect,
        collapse_secondary_doses=not args.secondary_keep_all_doses,
    )


if __name__ == "__main__":
    main()
