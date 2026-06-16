import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { TrialSitesMapSection } from "./TrialSitesMap.jsx";
import "./App.css";

const GENE_PROTEIN_INFO = {
  EGFR: {
    protein: "EGFR tyrosine kinase",
    pathway: "Receptor tyrosine kinase signaling",
    role: "Cell-surface receptor that activates RAS/MAPK and PI3K pathways.",
  },
  ERBB2: {
    protein: "HER2 tyrosine kinase",
    pathway: "Receptor tyrosine kinase signaling",
    role: "ERBB2/HER2 receptor tyrosine kinase; rare but actionable in NSCLC.",
  },
  MET: {
    protein: "MET receptor tyrosine kinase",
    pathway: "Receptor tyrosine kinase signaling",
    role: "HGF receptor; exon 14 skipping and amplification drive oncogenic signaling.",
  },
  ALK: {
    protein: "ALK tyrosine kinase",
    pathway: "Receptor tyrosine kinase signaling",
    role: "Fusion-driven kinase activated by ALK rearrangements.",
  },
  ROS1: {
    protein: "ROS1 tyrosine kinase",
    pathway: "Receptor tyrosine kinase signaling",
    role: "Fusion-driven kinase similar to ALK in NSCLC.",
  },
  RET: {
    protein: "RET receptor tyrosine kinase",
    pathway: "Receptor tyrosine kinase signaling",
    role: "Fusion or mutation-driven kinase in a subset of lung cancers.",
  },
  KRAS: {
    protein: "KRAS GTPase",
    pathway: "RAS-RAF-MEK signaling",
    role: "Small GTPase relaying growth signals; G12C and other variants are common NSCLC drivers.",
  },
  NRAS: {
    protein: "NRAS GTPase",
    pathway: "RAS-RAF-MEK signaling",
    role: "RAS family GTPase in the MAPK cascade.",
  },
  BRAF: {
    protein: "BRAF serine/threonine kinase",
    pathway: "RAS-RAF-MEK signaling",
    role: "Downstream RAF kinase in the MAPK pathway.",
  },
  MAP2K1: {
    protein: "MEK1 kinase",
    pathway: "RAS-RAF-MEK signaling",
    role: "MEK1 (MAP2K1) kinase transmitting signals to ERK.",
  },
  PIK3CA: {
    protein: "PI3K catalytic subunit alpha",
    pathway: "PI3K-AKT signaling",
    role: "Catalytic subunit of class I PI3K promoting cell survival and growth.",
  },
  TP53: {
    protein: "p53 tumor suppressor",
    pathway: "Cell-cycle and DNA-damage control",
    role: "Guardian of the genome; loss of p53 function is common in NSCLC.",
    regulates: "Brakes proliferation and triggers apoptosis when MAPK/PI3K signaling causes DNA damage.",
    relatedPathways: ["RAS-RAF-MEK", "PI3K-AKT"],
  },
  STK11: {
    protein: "LKB1 serine/threonine kinase",
    pathway: "Tumor suppressor / energy sensing",
    role: "Also known as LKB1; regulates AMPK and influences immunotherapy response.",
    regulates: "Metabolic brake on mTOR downstream of PI3K; frequently co-mutated with KRAS.",
    relatedPathways: ["PI3K-AKT", "RAS-RAF-MEK"],
  },
  KEAP1: {
    protein: "KEAP1 adaptor protein",
    pathway: "Oxidative stress response",
    role: "Negative regulator of NRF2; mutations can increase oxidative stress tolerance.",
    regulates: "Buffers ROS and stress created by active RTK and RAS signaling.",
    relatedPathways: ["Receptor tyrosine kinases", "RAS-RAF-MEK"],
  },
};

const MOLECULAR_PATHWAY_LAYOUT = {
  rtk: {
    id: "rtk",
    label: "Receptor tyrosine kinases",
    summary: "Cell-surface receptors receive growth signals and activate downstream cascades.",
    genes: ["EGFR", "ERBB2", "MET", "ALK", "ROS1", "RET"],
  },
  mapk: {
    id: "mapk",
    label: "RAS-RAF-MEK cascade",
    summary: "MAPK proliferation pathway: RAS → RAF → MEK → ERK.",
    steps: [["KRAS", "NRAS"], ["BRAF"], ["MAP2K1"]],
    downstreamLabel: "MEK → ERK → proliferation",
  },
  pi3k: {
    id: "pi3k",
    label: "PI3K-AKT signaling",
    summary: "Parallel survival arm activated by the same RTKs.",
    genes: ["PIK3CA"],
    downstreamLabel: "PI3K → AKT → mTOR",
  },
  suppressor: {
    id: "suppressor",
    label: "Tumor suppressors",
    summary: "Regulatory brakes that limit damage, metabolism, and stress from oncogenic signaling.",
    genes: ["TP53", "STK11", "KEAP1"],
  },
};

const PATHWAY_CONNECTIONS = [
  {
    id: "rtk-mapk",
    from: "rtk",
    to: "mapk",
    label: "Activates RAS",
    detail: "RTK phosphorylation recruits SOS to turn on KRAS/NRAS.",
  },
  {
    id: "rtk-pi3k",
    from: "rtk",
    to: "pi3k",
    label: "Activates PI3K",
    detail: "RTKs also recruit PI3K to drive AKT/mTOR survival signaling.",
  },
  {
    id: "mapk-outcome",
    from: "mapk",
    to: "outcome",
    label: "Drives proliferation",
    detail: "ERK transcription program promotes cell division.",
  },
  {
    id: "pi3k-outcome",
    from: "pi3k",
    to: "outcome",
    label: "Promotes survival",
    detail: "AKT/mTOR supports growth and blocks apoptosis.",
  },
];

const SUPPRESSOR_RELATIONSHIPS = [
  {
    gene: "TP53",
    targets: ["mapk", "pi3k"],
    label: "Brakes MAPK & PI3K output",
    detail: "p53 responds to replication stress and DNA damage caused by hyperactive growth signaling.",
  },
  {
    gene: "STK11",
    targets: ["pi3k", "mapk"],
    label: "Metabolic brake on mTOR / KRAS tumors",
    detail: "LKB1-AMPK restrains mTOR and is often lost together with KRAS mutations.",
  },
  {
    gene: "KEAP1",
    targets: ["rtk", "mapk"],
    label: "Buffers RTK/RAS oxidative stress",
    detail: "KEAP1 loss activates NRF2, helping cells tolerate ROS from oncogenic signaling.",
  },
];

const ANALYSIS_STEPS = [
  "Extracting eligibility criteria",
  "Searching NSCLC patient cohort",
  "Matching treatments and survival",
  "Searching active and completed trials",
  "Mapping matched trial sites",
  "Looking up existing therapies",
  "Searching DepMap cell lines",
  "Generating feasibility summary",
];

const EXAMPLE_PROTOCOL = `Patients must have metastatic non-small cell lung cancer.
Eligible patients must have KRAS G12C mutation confirmed by tumor tissue or ctDNA.
Patients with EGFR activating mutations or ALK fusions are excluded.
Prior platinum-based chemotherapy is required.
ECOG performance status must be 0 or 1.`;

function apiUrl(path) {
  const base = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
  return `${base}${path}`;
}

async function parseJsonResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const text = await response.text();
    const preview = text.replace(/\s+/g, " ").slice(0, 120);
    if (!import.meta.env.VITE_API_URL) {
      throw new Error(
        "VITE_API_URL is not set on Vercel. Add your Railway URL and redeploy."
      );
    }
    throw new Error(
      `API returned non-JSON (${response.status}). Check VITE_API_URL. ${preview}`
    );
  }
  return response.json();
}

function formatList(items) {
  if (!items || items.length === 0) {
    return "None";
  }
  return items.join(", ");
}

function AnalysisProgressPanel({ steps, activeStep }) {
  const completed = activeStep >= steps.length;
  const progressPercent = completed
    ? 100
    : Math.min(92, Math.round(((activeStep + 1) / steps.length) * 100));

  return (
    <div
      className="analysis-progress panel panel-wide"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="analysis-progress-header">
        <h2 className="analysis-progress-title">Running analysis</h2>
        <span className="analysis-progress-percent">{progressPercent}%</span>
      </div>

      <div className="analysis-progress-track" aria-hidden="true">
        <div
          className="analysis-progress-fill"
          style={{ width: `${progressPercent}%` }}
        />
      </div>

      <ul className="analysis-progress-steps">
        {steps.map((label, index) => {
          const isDone = index < activeStep || completed;
          const isActive = !completed && index === activeStep;

          return (
            <li
              key={label}
              className={[
                "analysis-progress-step",
                isDone ? "is-done" : "",
                isActive ? "is-active" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <span className="analysis-progress-step-icon" aria-hidden="true">
                {isDone ? "✓" : isActive ? "…" : ""}
              </span>
              <span>{label}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function StatCard({ label, value, hint }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value?.toLocaleString?.() ?? value}</div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  );
}

const STATUS_COLORS = {
  Living: "#7d9b6a",
  Deceased: "#b87d5f",
  Unknown: "#c9bcae",
};

function OsStatusPieChart({ distribution }) {
  const knownStatuses = (distribution || []).filter(
    (item) => item.status !== "Unknown"
  );
  const knownTotal = knownStatuses.reduce((sum, item) => sum + item.count, 0);

  if (!knownTotal) {
    return <p className="chart-empty">No overall survival status data available.</p>;
  }

  const normalized = knownStatuses.map((item) => ({
    ...item,
    percentage: Math.round((1000 * item.count) / knownTotal) / 10,
  }));

  let cumulative = 0;
  const gradientStops = normalized
    .map((item) => {
      const start = cumulative;
      cumulative += item.percentage;
      const color = STATUS_COLORS[item.status] || "#9a845f";
      return `${color} ${start}% ${cumulative}%`;
    })
    .join(", ");

  return (
    <div className="pie-chart-layout">
      <div
        className="pie-chart"
        style={{ background: `conic-gradient(${gradientStops})` }}
        aria-hidden="true"
      />
      <ul className="pie-legend">
        {normalized.map((item) => (
          <li key={item.status}>
            <span
              className="legend-swatch"
              style={{ background: STATUS_COLORS[item.status] || "#9a845f" }}
            />
            <span>
              {item.status}: {item.count.toLocaleString()} ({item.percentage}%)
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ControlBenchmarkStackedChart({ controlStats }) {
  const groups = [
    {
      key: "with_treatment",
      label: "With prior treatment",
      data: controlStats?.with_treatment,
    },
    {
      key: "without_treatment",
      label: "Without prior treatment",
      data: controlStats?.without_treatment,
    },
  ];
  const statusOrder = ["Living", "Deceased", "Unknown"];
  const labeledStatuses = new Set(["Living", "Deceased"]);
  const minSegmentLabelPercent = 10;
  const hasData = groups.some((group) => group.data?.patient_count > 0);

  if (!hasData) {
    return <p className="chart-empty">No NSCLC control benchmark data available.</p>;
  }

  return (
    <div className="control-benchmark-chart">
      <ul className="pie-legend control-benchmark-legend">
        {statusOrder.map((status) => (
          <li key={status}>
            <span
              className="legend-swatch"
              style={{ background: STATUS_COLORS[status] }}
            />
            <span>{status}</span>
          </li>
        ))}
      </ul>

      <div className="stacked-bar-list">
        {groups.map((group) => {
          const distribution = group.data?.os_status_distribution || [];
          const patientCount = group.data?.patient_count ?? 0;
          const avgDays = group.data?.average_survival_days;

          return (
            <div key={group.key} className="stacked-bar-row">
              <div className="stacked-bar-label">{group.label}</div>
              <div
                className="stacked-bar-track"
                role="img"
                aria-label={`${group.label} survival status distribution`}
              >
                {distribution.map((item) => {
                  const showLabel =
                    labeledStatuses.has(item.status) &&
                    item.percentage >= minSegmentLabelPercent;

                  return (
                    <div
                      key={item.status}
                      className={
                        showLabel
                          ? "stacked-bar-segment stacked-bar-segment-labeled"
                          : "stacked-bar-segment"
                      }
                      style={{
                        width: `${item.percentage}%`,
                        background: STATUS_COLORS[item.status] || "#9a845f",
                      }}
                      title={`${item.status}: ${item.count.toLocaleString()} (${item.percentage}%)`}
                    >
                      {showLabel ? (
                        <span className="stacked-bar-segment-label">
                          {item.percentage}%
                        </span>
                      ) : null}
                    </div>
                  );
                })}
              </div>
              <div className="stacked-bar-meta">
                n={patientCount.toLocaleString()}
                {avgDays != null ? ` · avg ${avgDays.toLocaleString()} days` : ""}
              </div>
            </div>
          );
        })}
      </div>

      <p className="control-benchmark-footnote">
        Unknown OS status is higher in untreated cohort.
      </p>
    </div>
  );
}

function OsDaysHistogram({ distribution }) {
  if (!distribution?.length) {
    return <p className="chart-empty">No overall survival days data available.</p>;
  }

  const maxCount = Math.max(...distribution.map((bin) => bin.count), 1);

  return (
    <div className="histogram">
      {distribution.map((bin) => (
        <div className="histogram-row" key={bin.label}>
          <div className="histogram-label">{bin.label}</div>
          <div className="histogram-bar-wrap">
            <div
              className="histogram-bar"
              style={{ width: `${(bin.count / maxCount) * 100}%` }}
            />
          </div>
          <div className="histogram-count">{bin.count.toLocaleString()}</div>
        </div>
      ))}
    </div>
  );
}

const METADATA_ATTRIBUTE_TABS = [
  { key: "sex", label: "Sex", dataKey: "sex_by_os_status", rowLabelKey: "value" },
  { key: "race", label: "Race", dataKey: "race_by_os_status", rowLabelKey: "value" },
  {
    key: "smoking_status",
    label: "Smoking",
    dataKey: "smoking_status_by_os_status",
    rowLabelKey: "value",
  },
  { key: "stage", label: "Stage", dataKey: "stage_by_os_status", rowLabelKey: "value" },
  {
    key: "ecog_status",
    label: "ECOG",
    dataKey: "ecog_status_by_os_status",
    rowLabelKey: "value",
  },
  { key: "age", label: "Age", dataKey: "age_by_os_status", rowLabelKey: "label" },
];

const OS_STATUS_ORDER = ["Living", "Deceased", "Unknown"];
const STAGE_ORDER = ["I", "II", "III", "IV", "Other"];

function orderOsStatusDistribution(distribution, patientCount) {
  const byStatus = new Map((distribution || []).map((item) => [item.status, item]));
  const total =
    patientCount ||
    OS_STATUS_ORDER.reduce((sum, status) => sum + (byStatus.get(status)?.count || 0), 0) ||
    1;

  return OS_STATUS_ORDER.map((status) => {
    const item = byStatus.get(status);
    const count = item?.count ?? 0;
    return {
      status,
      count,
      percentage: total ? Math.round((1000 * count) / total) / 10 : 0,
    };
  }).filter((item) => item.count > 0);
}

function sortMetadataAttributeRows(rows, activeAttr, rowLabelKey) {
  if (activeAttr !== "stage") {
    return rows;
  }

  const orderIndex = Object.fromEntries(STAGE_ORDER.map((label, index) => [label, index]));
  return [...rows].sort((left, right) => {
    const leftLabel = left[rowLabelKey] || "";
    const rightLabel = right[rowLabelKey] || "";
    const leftRank = orderIndex[leftLabel] ?? STAGE_ORDER.length;
    const rightRank = orderIndex[rightLabel] ?? STAGE_ORDER.length;
    if (leftRank !== rightRank) {
      return leftRank - rightRank;
    }
    return leftLabel.localeCompare(rightLabel);
  });
}

function PatientAttributesByOsStatusChart({ metadataStats, activeAttr, onAttrChange }) {
  const activeTab =
    METADATA_ATTRIBUTE_TABS.find((tab) => tab.key === activeAttr) ||
    METADATA_ATTRIBUTE_TABS[0];
  const rows = sortMetadataAttributeRows(
    metadataStats?.[activeTab.dataKey] || [],
    activeAttr,
    activeTab.rowLabelKey
  );
  const statusOrder = OS_STATUS_ORDER;
  const labeledStatuses = new Set(["Living", "Deceased"]);
  const minSegmentLabelPercent = 10;

  return (
    <div className="metadata-by-os-chart">
      <div className="attribute-tab-list" role="tablist" aria-label="Patient attribute">
        {METADATA_ATTRIBUTE_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={activeAttr === tab.key}
            className={activeAttr === tab.key ? "attribute-tab active" : "attribute-tab"}
            onClick={() => onAttrChange(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {!rows.length ? (
        <p className="chart-empty">
          No {activeTab.label.toLowerCase()} data with survival status available.
        </p>
      ) : (
        <>
          <ul className="pie-legend control-benchmark-legend">
            {statusOrder.map((status) => (
              <li key={status}>
                <span
                  className="legend-swatch"
                  style={{ background: STATUS_COLORS[status] }}
                />
                <span>{status}</span>
              </li>
            ))}
          </ul>

          <div className="stacked-bar-list">
            {rows.map((row) => {
              const patientCount = row.patient_count ?? 0;
              const distribution = orderOsStatusDistribution(
                row.os_status_distribution,
                patientCount
              );
              const rowLabel = row[activeTab.rowLabelKey];

              return (
                <div key={rowLabel} className="stacked-bar-row">
                  <div className="stacked-bar-label">{rowLabel}</div>
                  <div
                    className="stacked-bar-track"
                    role="img"
                    aria-label={`${rowLabel} survival status distribution`}
                  >
                    {distribution.map((item) => {
                      const showLabel =
                        labeledStatuses.has(item.status) &&
                        item.percentage >= minSegmentLabelPercent;

                      return (
                        <div
                          key={`${rowLabel}-${item.status}`}
                          className={
                            showLabel
                              ? "stacked-bar-segment stacked-bar-segment-labeled"
                              : "stacked-bar-segment"
                          }
                          style={{
                            width: `${item.percentage}%`,
                            background: STATUS_COLORS[item.status] || "#9a845f",
                          }}
                          title={`${item.status}: ${item.count.toLocaleString()} (${item.percentage}%)`}
                        >
                          {showLabel ? (
                            <span className="stacked-bar-segment-label">
                              {item.percentage}%
                            </span>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                  <div className="stacked-bar-meta">
                    n={patientCount.toLocaleString()}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

const TRIALS_PER_PAGE = 10;
const TRIAL_SUMMARY_COLORS = {
  total: "#6b8f71",
  active: "#7d9b6a",
  completed: "#9a845f",
};

const TRIAL_OUTCOME_COLORS = {
  positive: "#7d9b6a",
  negative: "#b87d5f",
  failed: "#c45c5c",
  inconclusive: "#c9bcae",
  noResults: "#8a9aa8",
};
const EVIDENCE_RANK = { A: 0, B: 1, C: 2, D: 3 };

function normalizeEvidenceLabel(label) {
  if (!label) {
    return null;
  }
  const letter = String(label).trim().toUpperCase().charAt(0);
  return Object.prototype.hasOwnProperty.call(EVIDENCE_RANK, letter) ? letter : null;
}

function normalizeResponseCategory(responseType) {
  if (!responseType) {
    return "unknown";
  }
  const value = String(responseType).toLowerCase();
  if (value.includes("sensitive") || value.includes("responsive")) {
    return "sensitive";
  }
  if (value.includes("resistant") || value.includes("non-response")) {
    return "resistant";
  }
  return "unknown";
}

function formatResponseLabel(category) {
  if (category === "sensitive") {
    return "Sensitive";
  }
  if (category === "resistant") {
    return "Resistant";
  }
  return "Unknown";
}

function deriveChipBadges(associations) {
  const sensitive = associations.filter(
    (association) => association.response_category === "sensitive"
  );
  const resistant = associations.filter(
    (association) => association.response_category === "resistant"
  );

  let primaryResponse = "unknown";
  let pool = associations;
  if (sensitive.length) {
    primaryResponse = "sensitive";
    pool = sensitive;
  } else if (resistant.length) {
    primaryResponse = "resistant";
    pool = resistant;
  }

  const bestEvidenceFromPool = (items) => {
    let best = null;
    for (const association of items) {
      const label = association.evidence_label;
      if (!label) {
        continue;
      }
      if (best === null || EVIDENCE_RANK[label] < EVIDENCE_RANK[best]) {
        best = label;
      }
    }
    return best;
  };

  let bestEvidence = bestEvidenceFromPool(pool) || bestEvidenceFromPool(associations);

  return {
    primaryResponse,
    bestEvidence,
    extraAssociationCount: Math.max(0, associations.length - 1),
  };
}

function pickReferenceUrl(...candidates) {
  for (const candidate of candidates) {
    if (!candidate) {
      continue;
    }
    if (Array.isArray(candidate)) {
      const nested = pickReferenceUrl(...candidate);
      if (nested) {
        return nested;
      }
      continue;
    }
    const text = String(candidate).trim();
    if (text.toLowerCase().startsWith("http")) {
      return text;
    }
  }
  return null;
}

function normalizeGeneSymbol(value) {
  return String(value || "").trim().toUpperCase();
}

function geneMatchesBiomarkerList(gene, biomarkers) {
  const target = normalizeGeneSymbol(gene);
  return (biomarkers || []).some((item) => {
    const text = normalizeGeneSymbol(item);
    return text === target || text.startsWith(`${target} `);
  });
}

function getGeneProteinInfo(gene) {
  const symbol = normalizeGeneSymbol(gene);
  const known = GENE_PROTEIN_INFO[symbol];
  if (known) {
    return { gene: symbol, ...known };
  }
  return {
    gene: symbol,
    protein: `${symbol} protein`,
    pathway: "NSCLC genomic landscape",
    role: "Gene tracked in the NSCLC cohort mutation panel.",
  };
}

function buildGenePathwayData(genePatientCounts, depmap) {
  const byGene = {};

  for (const row of genePatientCounts || []) {
    const gene = normalizeGeneSymbol(row.gene);
    byGene[gene] = { ...row, gene };
  }

  for (const row of depmap?.gene_effect_summary || []) {
    const gene = normalizeGeneSymbol(row.gene_symbol);
    byGene[gene] = {
      ...(byGene[gene] || { gene }),
      mean_gene_effect: row.mean_gene_effect,
      min_gene_effect: row.min_gene_effect,
      max_gene_effect: row.max_gene_effect,
      gene_effect_model_count: row.model_count,
    };
  }

  for (const row of depmap?.gene_cell_line_counts || []) {
    const gene = normalizeGeneSymbol(row.gene_symbol);
    if (!byGene[gene]) {
      byGene[gene] = { gene };
    }
    byGene[gene].cell_lines_with_mutation = row.cell_lines_with_mutation;
  }

  return Object.values(byGene);
}

function interpretGeneEffect(meanEffect) {
  if (meanEffect == null || Number.isNaN(Number(meanEffect))) {
    return null;
  }
  const value = Number(meanEffect);
  if (value <= -0.5) {
    return { level: "strong", label: "Strong dependency" };
  }
  if (value <= -0.1) {
    return { level: "moderate", label: "Moderate dependency" };
  }
  return { level: "weak", label: "Weak / buffered" };
}

function formatGeneEffectValue(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return null;
  }
  const numeric = Number(value);
  return numeric > 0 ? `+${numeric.toFixed(2)}` : numeric.toFixed(2);
}

function buildGeneNode(gene, geneDataBySymbol) {
  const symbol = normalizeGeneSymbol(gene);
  return {
    gene: symbol,
    data: geneDataBySymbol[symbol],
    info: getGeneProteinInfo(symbol),
  };
}

function buildMolecularPathwayLayout(genePatientCounts, depmap) {
  const geneRows = buildGenePathwayData(genePatientCounts, depmap);
  const availableGenes = new Set(
    geneRows.map((row) => normalizeGeneSymbol(row.gene))
  );
  const geneDataBySymbol = Object.fromEntries(
    geneRows.map((row) => [normalizeGeneSymbol(row.gene), row])
  );

  const rtkNodes = MOLECULAR_PATHWAY_LAYOUT.rtk.genes
    .filter((gene) => availableGenes.has(normalizeGeneSymbol(gene)))
    .map((gene) => buildGeneNode(gene, geneDataBySymbol));

  const mapkSteps = MOLECULAR_PATHWAY_LAYOUT.mapk.steps
    .map((step) =>
      step
        .filter((gene) => availableGenes.has(normalizeGeneSymbol(gene)))
        .map((gene) => buildGeneNode(gene, geneDataBySymbol))
    )
    .filter((step) => step.length > 0);

  const pi3kNodes = MOLECULAR_PATHWAY_LAYOUT.pi3k.genes
    .filter((gene) => availableGenes.has(normalizeGeneSymbol(gene)))
    .map((gene) => buildGeneNode(gene, geneDataBySymbol));

  const suppressorNodes = MOLECULAR_PATHWAY_LAYOUT.suppressor.genes
    .filter((gene) => availableGenes.has(normalizeGeneSymbol(gene)))
    .map((gene) => buildGeneNode(gene, geneDataBySymbol));

  const assignedGenes = new Set([
    ...rtkNodes.map((node) => node.gene),
    ...mapkSteps.flatMap((step) => step.map((node) => node.gene)),
    ...pi3kNodes.map((node) => node.gene),
    ...suppressorNodes.map((node) => node.gene),
  ]);

  const otherNodes = [...availableGenes]
    .filter((gene) => !assignedGenes.has(gene))
    .sort()
    .map((gene) => buildGeneNode(gene, geneDataBySymbol));

  const hasSignaling = rtkNodes.length || mapkSteps.length || pi3kNodes.length;
  if (!hasSignaling && !suppressorNodes.length && !otherNodes.length) {
    return null;
  }

  const activeLayerIds = new Set();
  if (rtkNodes.length) {
    activeLayerIds.add("rtk");
  }
  if (mapkSteps.length) {
    activeLayerIds.add("mapk");
  }
  if (pi3kNodes.length) {
    activeLayerIds.add("pi3k");
  }
  if (rtkNodes.length && (mapkSteps.length || pi3kNodes.length)) {
    activeLayerIds.add("outcome");
  }

  const connections = PATHWAY_CONNECTIONS.filter(
    (connection) =>
      activeLayerIds.has(connection.from) && activeLayerIds.has(connection.to)
  );

  const suppressorRelationships = SUPPRESSOR_RELATIONSHIPS.filter((relationship) =>
    suppressorNodes.some((node) => node.gene === relationship.gene)
  ).map((relationship) => ({
    ...relationship,
    activeTargets: relationship.targets.filter((target) => activeLayerIds.has(target)),
  }));

  return {
    rtk: rtkNodes.length ? { ...MOLECULAR_PATHWAY_LAYOUT.rtk, nodes: rtkNodes } : null,
    mapk: mapkSteps.length ? { ...MOLECULAR_PATHWAY_LAYOUT.mapk, steps: mapkSteps } : null,
    pi3k: pi3kNodes.length ? { ...MOLECULAR_PATHWAY_LAYOUT.pi3k, nodes: pi3kNodes } : null,
    suppressor: suppressorNodes.length
      ? {
          ...MOLECULAR_PATHWAY_LAYOUT.suppressor,
          nodes: suppressorNodes,
          relationships: suppressorRelationships,
        }
      : null,
    other: otherNodes.length ? { label: "Other tracked genes", nodes: otherNodes } : null,
    connections,
  };
}

function formatMutationRate(row) {
  const withMutation = row?.patients_with_mutation ?? 0;
  const withoutMutation = row?.patients_without_mutation ?? 0;
  const total = withMutation + withoutMutation;
  if (!total) {
    return "0%";
  }
  return `${((withMutation / total) * 100).toFixed(1)}%`;
}

function PathwayConnection({ connection, variant = "activate" }) {
  return (
    <div className={`gene-pathway-connection gene-pathway-connection-${variant}`}>
      <span className="gene-pathway-connection-line" aria-hidden="true" />
      <div className="gene-pathway-connection-label">
        <strong>{connection.label}</strong>
        <span>{connection.detail}</span>
      </div>
    </div>
  );
}

function GenePathwayNode({ node, status, isActive, onToggle, layerId }) {
  const { gene, info, data } = node;
  const mutationRate = formatMutationRate(data);
  const geneEffectInterpretation = interpretGeneEffect(data?.mean_gene_effect);
  const suppressorRelationship = SUPPRESSOR_RELATIONSHIPS.find(
    (relationship) => relationship.gene === gene
  );

  return (
    <div className="gene-pathway-node-wrap">
      <button
        type="button"
        className={[
          "gene-pathway-node",
          layerId ? `gene-pathway-node-layer-${layerId}` : "",
          status ? `gene-pathway-node-${status}` : "",
          isActive ? "gene-pathway-node-active" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        aria-expanded={isActive}
        aria-describedby={isActive ? `gene-pathway-tooltip-${gene}` : undefined}
        onClick={() => onToggle(gene)}
      >
        <span className="gene-pathway-node-protein">{info.protein}</span>
        <span className="gene-pathway-node-gene">
          {gene}
          {geneEffectInterpretation?.level === "strong" ? (
            <span
              className="gene-pathway-dependency-star"
              title="Strong DepMap dependency (mean gene effect ≤ −0.5)"
              aria-label="Strong DepMap dependency"
            >
              ★
            </span>
          ) : null}
        </span>
        <span className="gene-pathway-node-rate">{mutationRate} mutated</span>
      </button>
      {isActive ? (
        <div
          id={`gene-pathway-tooltip-${gene}`}
          className="gene-pathway-tooltip"
          role="tooltip"
        >
          <p className="gene-pathway-tooltip-title">{info.protein}</p>
          <dl className="gene-pathway-tooltip-meta">
            <div>
              <dt>Gene</dt>
              <dd>{gene}</dd>
            </div>
            <div>
              <dt>Pathway</dt>
              <dd>{info.pathway}</dd>
            </div>
            <div>
              <dt>Role</dt>
              <dd>{info.role}</dd>
            </div>
            {info.regulates ? (
              <div>
                <dt>Regulates</dt>
                <dd>{info.regulates}</dd>
              </div>
            ) : null}
            {info.relatedPathways?.length ? (
              <div>
                <dt>Related pathways</dt>
                <dd>{info.relatedPathways.join(", ")}</dd>
              </div>
            ) : null}
            {layerId === "rtk" ? (
              <div>
                <dt>Activates</dt>
                <dd>RAS-RAF-MEK cascade and PI3K-AKT signaling</dd>
              </div>
            ) : null}
            {suppressorRelationship ? (
              <div>
                <dt>Pathway relationship</dt>
                <dd>{suppressorRelationship.detail}</dd>
              </div>
            ) : null}
            <div>
              <dt>With mutation</dt>
              <dd>{(data?.patients_with_mutation ?? 0).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Without mutation</dt>
              <dd>{(data?.patients_without_mutation ?? 0).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Mutation rate</dt>
              <dd>{mutationRate}</dd>
            </div>
            {status ? (
              <div>
                <dt>Protocol status</dt>
                <dd>{status === "required" ? "Required biomarker" : "Excluded biomarker"}</dd>
              </div>
            ) : null}
            {geneEffectInterpretation ? (
              <>
                <div className="gene-pathway-tooltip-divider">DepMap · eligible cell lines</div>
                <div>
                  <dt>Mean gene effect</dt>
                  <dd className="gene-pathway-gene-effect">
                    <span
                      className={`gene-effect-value gene-effect-${geneEffectInterpretation.level}`}
                    >
                      {formatGeneEffectValue(data.mean_gene_effect)}
                    </span>
                    <span
                      className={`gene-effect-label gene-effect-${geneEffectInterpretation.level}`}
                    >
                      {geneEffectInterpretation.label}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>Gene effect range</dt>
                  <dd>
                    {formatGeneEffectValue(data.min_gene_effect)} to{" "}
                    {formatGeneEffectValue(data.max_gene_effect)}
                  </dd>
                </div>
                <div>
                  <dt>Cell lines with effect data</dt>
                  <dd>{(data.gene_effect_model_count ?? 0).toLocaleString()}</dd>
                </div>
              </>
            ) : null}
          </dl>
        </div>
      ) : null}
    </div>
  );
}

function GenePathwayNodeRow({ nodes, layerId, geneStatus, activeGene, onToggle }) {
  if (!nodes?.length) {
    return null;
  }

  return (
    <div className="gene-pathway-node-row">
      {nodes.map((node) => (
        <GenePathwayNode
          key={`${layerId}-${node.gene}`}
          node={node}
          layerId={layerId}
          status={geneStatus(node.gene)}
          isActive={activeGene === node.gene}
          onToggle={onToggle}
        />
      ))}
    </div>
  );
}

function GeneMolecularPathwaySection({
  genePatientCounts,
  depmap,
  eligibility,
  expanded,
  onToggle,
}) {
  const [activeGene, setActiveGene] = useState("");
  const layout = useMemo(
    () => buildMolecularPathwayLayout(genePatientCounts, depmap),
    [genePatientCounts, depmap]
  );

  useEffect(() => {
    if (!expanded) {
      setActiveGene("");
    }
  }, [expanded]);

  if (!layout) {
    return null;
  }

  const geneStatus = (gene) => {
    if (geneMatchesBiomarkerList(gene, eligibility?.required_biomarkers)) {
      return "required";
    }
    if (geneMatchesBiomarkerList(gene, eligibility?.excluded_biomarkers)) {
      return "excluded";
    }
    return "";
  };

  const handleToggle = (gene) =>
    setActiveGene((current) => (current === gene ? "" : gene));

  const geneCount = buildGenePathwayData(genePatientCounts, depmap).length;
  const eligibleCellLines = depmap?.eligible_cell_lines;

  const layerLabel = {
    rtk: "RTK",
    mapk: "MAPK",
    pi3k: "PI3K",
  };

  return (
    <article className="panel panel-wide panel-collapsible panel-gene-pathway">
      <button
        type="button"
        className="panel-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="panel-toggle-text">
          <span className="panel-toggle-title">Molecular pathway map</span>
          <span className="panel-toggle-meta">
            {geneCount} genes
            {eligibleCellLines != null
              ? ` · DepMap effects from ${eligibleCellLines.toLocaleString()} eligible cell lines`
              : " in cBioPortal"}
          </span>
        </span>
        <span className="panel-toggle-icon" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>
      {expanded ? (
        <div className="gene-pathway-wrap">
          <p className="gene-pathway-intro">
            RTKs activate two parallel arms: RAS-RAF-MEK (proliferation) and
            PI3K-AKT (survival). Tumor suppressors brake the consequences of
            hyperactive signaling. Click any node for gene details.
          </p>
          <div className="gene-pathway-legend">
            <div className="gene-pathway-legend-row">
              <span className="gene-pathway-legend-item">
                <span className="gene-pathway-legend-swatch gene-pathway-legend-required" />
                Required biomarker
              </span>
              <span className="gene-pathway-legend-item">
                <span className="gene-pathway-legend-swatch gene-pathway-legend-excluded" />
                Excluded biomarker
              </span>
              <span className="gene-pathway-legend-item">
                <span className="gene-pathway-legend-line gene-pathway-legend-line-activate" />
                Activation
              </span>
              <span className="gene-pathway-legend-item">
                <span className="gene-pathway-legend-line gene-pathway-legend-line-regulate" />
                Suppressor regulation
              </span>
            </div>
            {depmap?.gene_effect_summary?.length ? (
              <div className="gene-pathway-legend-row">
                <span className="gene-pathway-legend-item">
                  <span
                    className="gene-pathway-dependency-star gene-pathway-legend-star"
                    aria-hidden="true"
                  >
                    ★
                  </span>
                  Strong DepMap dependency
                </span>
                <span className="gene-pathway-legend-item">
                  <span className="gene-pathway-legend-swatch gene-effect-legend-moderate" />
                  Moderate dependency
                </span>
                <span className="gene-pathway-legend-item">
                  <span className="gene-pathway-legend-swatch gene-effect-legend-weak" />
                  Weak / buffered
                </span>
              </div>
            ) : null}
          </div>

          <div className="gene-pathway-diagram">
            {layout.rtk ? (
              <section className="gene-pathway-layer gene-pathway-layer-rtk">
                <header className="gene-pathway-layer-header">
                  <h3>{layout.rtk.label}</h3>
                  <p>{layout.rtk.summary}</p>
                </header>
                <GenePathwayNodeRow
                  nodes={layout.rtk.nodes}
                  layerId="rtk"
                  geneStatus={geneStatus}
                  activeGene={activeGene}
                  onToggle={handleToggle}
                />
              </section>
            ) : null}

            {layout.connections.some((connection) => connection.from === "rtk") ? (
              <div className="gene-pathway-branch-grid">
                {layout.connections
                  .filter((connection) => connection.from === "rtk")
                  .map((connection) => (
                    <PathwayConnection
                      key={connection.id}
                      connection={connection}
                      variant={connection.to === "pi3k" ? "branch" : "activate"}
                    />
                  ))}
              </div>
            ) : null}

            <div className="gene-pathway-signaling-split">
              {layout.mapk ? (
                <section className="gene-pathway-layer gene-pathway-layer-mapk">
                  <header className="gene-pathway-layer-header">
                    <h3>{layout.mapk.label}</h3>
                    <p>{layout.mapk.summary}</p>
                  </header>
                  <div className="gene-pathway-flow">
                    {layout.mapk.steps.map((step, stepIndex) => (
                      <div key={`mapk-${stepIndex}`} className="gene-pathway-step">
                        {stepIndex > 0 ? (
                          <span className="gene-pathway-connector" aria-hidden="true">
                            →
                          </span>
                        ) : null}
                        <GenePathwayNodeRow
                          nodes={step}
                          layerId="mapk"
                          geneStatus={geneStatus}
                          activeGene={activeGene}
                          onToggle={handleToggle}
                        />
                      </div>
                    ))}
                  </div>
                  {layout.mapk.downstreamLabel ? (
                    <p className="gene-pathway-downstream">{layout.mapk.downstreamLabel}</p>
                  ) : null}
                </section>
              ) : null}

              {layout.pi3k ? (
                <section className="gene-pathway-layer gene-pathway-layer-pi3k">
                  <header className="gene-pathway-layer-header">
                    <h3>{layout.pi3k.label}</h3>
                    <p>{layout.pi3k.summary}</p>
                  </header>
                  <GenePathwayNodeRow
                    nodes={layout.pi3k.nodes}
                    layerId="pi3k"
                    geneStatus={geneStatus}
                    activeGene={activeGene}
                    onToggle={handleToggle}
                  />
                  {layout.pi3k.downstreamLabel ? (
                    <p className="gene-pathway-downstream">{layout.pi3k.downstreamLabel}</p>
                  ) : null}
                </section>
              ) : null}
            </div>

            {layout.connections.some((connection) => connection.to === "outcome") ? (
              <section className="gene-pathway-outcome">
                <h3 className="gene-pathway-outcome-title">Shared tumor growth output</h3>
                <div className="gene-pathway-outcome-links">
                  {layout.connections
                    .filter((connection) => connection.to === "outcome")
                    .map((connection) => (
                      <div key={connection.id} className="gene-pathway-outcome-item">
                        <span className="gene-pathway-outcome-from">
                          {connection.from === "mapk" ? "MAPK arm" : "PI3K arm"}
                        </span>
                        <span className="gene-pathway-outcome-arrow" aria-hidden="true">
                          →
                        </span>
                        <span>{connection.label}</span>
                      </div>
                    ))}
                </div>
              </section>
            ) : null}

            {layout.suppressor ? (
              <section className="gene-pathway-layer gene-pathway-layer-suppressor">
                <header className="gene-pathway-layer-header">
                  <h3>{layout.suppressor.label}</h3>
                  <p>{layout.suppressor.summary}</p>
                </header>
                <div className="gene-pathway-suppressor-grid">
                  {layout.suppressor.nodes.map((node) => {
                    const relationship = layout.suppressor.relationships.find(
                      (item) => item.gene === node.gene
                    );
                    return (
                      <div key={node.gene} className="gene-pathway-suppressor-card">
                        <GenePathwayNode
                          node={node}
                          layerId="suppressor"
                          status={geneStatus(node.gene)}
                          isActive={activeGene === node.gene}
                          onToggle={handleToggle}
                        />
                        {relationship ? (
                          <div className="gene-pathway-regulation">
                            <p className="gene-pathway-regulation-label">{relationship.label}</p>
                            <div className="gene-pathway-regulation-targets">
                              {relationship.activeTargets.map((target) => (
                                <span
                                  key={`${node.gene}-${target}`}
                                  className="gene-pathway-regulation-target"
                                >
                                  regulates {layerLabel[target] || target}
                                </span>
                              ))}
                            </div>
                            <p className="gene-pathway-regulation-detail">{relationship.detail}</p>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </section>
            ) : null}

            {layout.other ? (
              <section className="gene-pathway-layer gene-pathway-layer-other">
                <header className="gene-pathway-layer-header">
                  <h3>{layout.other.label}</h3>
                </header>
                <GenePathwayNodeRow
                  nodes={layout.other.nodes}
                  layerId="other"
                  geneStatus={geneStatus}
                  activeGene={activeGene}
                  onToggle={handleToggle}
                />
              </section>
            ) : null}
          </div>
        </div>
      ) : null}
    </article>
  );
}

function buildDrugEntries(matchedDrugs) {
  const byName = new Map();

  for (const treatment of matchedDrugs || []) {
    for (const drug of treatment.drugs || []) {
      const name = drug.drug_name;
      if (!name) {
        continue;
      }

      const association = {
        evidence_label: normalizeEvidenceLabel(treatment.evidence_label),
        evidence_level: treatment.evidence_level ?? null,
        response_type: treatment.response_type ?? null,
        response_category: normalizeResponseCategory(treatment.response_type),
        cancer_type: treatment.cancer_type ?? null,
        description: treatment.description ?? null,
        source_link: treatment.source_link ?? null,
        publication_url: treatment.publication_url ?? null,
        url: pickReferenceUrl(drug.url, treatment.url, treatment.source_link, treatment.publication_url),
        mutation_type_badges: treatment.mutation_type_badges || [],
      };

      if (!byName.has(name)) {
        byName.set(name, {
          name,
          url: association.url,
          associations: [],
          associationKeys: new Set(),
          mutationTypeBadges: new Set(),
        });
      }

      const entry = byName.get(name);
      if (!entry.url && association.url) {
        entry.url = association.url;
      }
      for (const badge of association.mutation_type_badges) {
        entry.mutationTypeBadges.add(badge);
      }
      const dedupeKey = [
        association.evidence_label,
        association.response_category,
        association.cancer_type,
        association.description,
        (association.mutation_type_badges || []).join(","),
      ].join("|");
      if (entry.associationKeys.has(dedupeKey)) {
        continue;
      }
      entry.associationKeys.add(dedupeKey);
      entry.associations.push(association);
    }
  }

  return [...byName.values()]
    .map(({ associationKeys, mutationTypeBadges, ...entry }) => ({
      ...entry,
      mutationTypeBadges: [...(mutationTypeBadges || [])].sort(),
      ...deriveChipBadges(entry.associations),
    }))
    .sort((left, right) => {
      const responseOrder = { sensitive: 0, resistant: 1, unknown: 2 };
      const responseDiff =
        responseOrder[left.primaryResponse] - responseOrder[right.primaryResponse];
      if (responseDiff !== 0) {
        return responseDiff;
      }

      const leftRank = left.bestEvidence ? EVIDENCE_RANK[left.bestEvidence] : 99;
      const rightRank = right.bestEvidence ? EVIDENCE_RANK[right.bestEvidence] : 99;
      if (leftRank !== rightRank) {
        return leftRank - rightRank;
      }

      return left.name.localeCompare(right.name);
    });
}

function DrugChipName({ drug }) {
  const tooltip = drug.url ? `View CIViC evidence: ${drug.url}` : undefined;

  return (
    <span className="drug-chip-name" title={tooltip}>
      {drug.name}
    </span>
  );
}

function DrugChipBadges({ drug }) {
  return (
    <>
      {drug.mutationTypeBadges?.map((badge) => (
        <span key={badge} className="drug-badge drug-badge-mutation">
          {badge}
        </span>
      ))}
      <span className={`drug-badge drug-badge-response drug-badge-${drug.primaryResponse}`}>
        {formatResponseLabel(drug.primaryResponse)}
      </span>
      {drug.bestEvidence ? (
        <span className="drug-badge drug-badge-evidence">
          Evidence {drug.bestEvidence}
        </span>
      ) : null}
      {drug.extraAssociationCount > 0 ? (
        <span className="drug-badge drug-badge-more">+{drug.extraAssociationCount}</span>
      ) : null}
    </>
  );
}

const PRISM_SCREEN_COLORS = {
  primary: "#6f8f72",
  secondary: "#8a7d9b",
};

function formatPrismDrugField(value) {
  const text = String(value ?? "").trim();
  if (!text || text.toUpperCase() === "NA") {
    return null;
  }
  return text;
}

function prismDrugRowKey(row) {
  const label = row.drug_name || row.broad_id || "unknown";
  return `${row.screen_type || "unknown"}-${row.broad_id || label}`;
}

function DepMapPrismDrugTooltip({ row, onClose, tooltipRef, style, tooltipId }) {
  const drugName = formatPrismDrugField(row.drug_name) || row.broad_id || "Unknown compound";
  const fields = [
    ["Broad ID", formatPrismDrugField(row.broad_id)],
    ["MOA", formatPrismDrugField(row.moa)],
    ["Target", formatPrismDrugField(row.target)],
    ["Indication", formatPrismDrugField(row.indication)],
    ["Disease area", formatPrismDrugField(row.disease_area)],
    ["Phase", formatPrismDrugField(row.phase)],
    ["Screen", formatPrismDrugField(row.screen_type)],
    [
      "Mean log-fold change",
      row.mean_log_fold_change != null ? Number(row.mean_log_fold_change).toFixed(2) : null,
    ],
    [
      "Best log-fold change",
      row.best_log_fold_change != null ? Number(row.best_log_fold_change).toFixed(2) : null,
    ],
    [
      "Sensitive cell lines",
      row.sensitive_model_count != null
        ? Number(row.sensitive_model_count).toLocaleString()
        : null,
    ],
  ].filter(([, value]) => value);

  return (
    <div
      ref={tooltipRef}
      id={tooltipId}
      className="prism-drug-tooltip"
      style={style}
      role="tooltip"
    >
      <div className="prism-drug-tooltip-header">
        <p className="prism-drug-tooltip-title">{drugName}</p>
        <button
          type="button"
          className="prism-drug-tooltip-close"
          onClick={onClose}
          aria-label={`Close ${drugName} details`}
        >
          Close
        </button>
      </div>
      <dl className="prism-drug-tooltip-meta">
        {fields.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function computePrismTooltipStyle(buttonEl, tooltipEl) {
  const margin = 12;
  const buttonRect = buttonEl.getBoundingClientRect();
  const tooltipWidth = tooltipEl.offsetWidth;
  const tooltipHeight = tooltipEl.offsetHeight;

  let top = buttonRect.bottom + margin;
  if (top + tooltipHeight > window.innerHeight - margin) {
    top = buttonRect.top - tooltipHeight - margin;
  }
  top = Math.max(margin, Math.min(top, window.innerHeight - tooltipHeight - margin));

  let left = buttonRect.left;
  if (left + tooltipWidth > window.innerWidth - margin) {
    left = window.innerWidth - tooltipWidth - margin;
  }
  left = Math.max(margin, left);

  return {
    position: "fixed",
    top: `${top}px`,
    left: `${left}px`,
    visibility: "visible",
    zIndex: 1000,
  };
}

const HIDDEN_PRISM_TOOLTIP_STYLE = {
  position: "fixed",
  top: "-9999px",
  left: "-9999px",
  visibility: "hidden",
  zIndex: 1000,
};

function PrismDrugNameButton({ row, rowKey, isActive, label, onToggle }) {
  const buttonRef = useRef(null);
  const tooltipRef = useRef(null);
  const [tooltipStyle, setTooltipStyle] = useState(HIDDEN_PRISM_TOOLTIP_STYLE);

  const updateTooltipPosition = useCallback(() => {
    const button = buttonRef.current;
    const tooltip = tooltipRef.current;
    if (!button || !tooltip) {
      return;
    }
    setTooltipStyle(computePrismTooltipStyle(button, tooltip));
  }, []);

  useLayoutEffect(() => {
    if (!isActive) {
      setTooltipStyle(HIDDEN_PRISM_TOOLTIP_STYLE);
      return undefined;
    }

    updateTooltipPosition();
    const frame = window.requestAnimationFrame(updateTooltipPosition);
    window.addEventListener("resize", updateTooltipPosition);
    window.addEventListener("scroll", updateTooltipPosition, true);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updateTooltipPosition);
      window.removeEventListener("scroll", updateTooltipPosition, true);
    };
  }, [isActive, rowKey, updateTooltipPosition]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className={`prism-drug-name-btn${isActive ? " prism-drug-name-btn-active" : ""}`}
        onClick={() => onToggle(rowKey)}
        aria-expanded={isActive}
        aria-describedby={isActive ? `prism-drug-tooltip-${rowKey}` : undefined}
      >
        {label}
      </button>
      {isActive
        ? createPortal(
            <DepMapPrismDrugTooltip
              row={row}
              tooltipRef={tooltipRef}
              tooltipId={`prism-drug-tooltip-${rowKey}`}
              style={tooltipStyle}
              onClose={() => onToggle(rowKey)}
            />,
            document.body
          )
        : null}
    </>
  );
}

function DepMapPrismSensitivityChart({ drugSummary, activeDrugKey, onDrugToggle }) {
  const rows = [...(drugSummary || [])].sort(
    (left, right) => left.mean_log_fold_change - right.mean_log_fold_change
  );

  if (!rows.length) {
    return (
      <p className="chart-empty">
        No PRISM sensitivity data for eligible DepMap cell lines.
      </p>
    );
  }

  const maxMagnitude = Math.max(
    ...rows.map((row) => Math.abs(Number(row.mean_log_fold_change) || 0)),
    0.01
  );

  return (
    <div className="prism-sensitivity-chart">
      {rows.map((row) => {
        const lfc = Number(row.mean_log_fold_change) || 0;
        const label = row.drug_name || row.broad_id || "Unknown compound";
        const screenType = row.screen_type || "unknown";
        const rowKey = prismDrugRowKey(row);
        const isActive = activeDrugKey === rowKey;
        return (
          <div className="prism-sensitivity-row" key={rowKey}>
            <div className="prism-sensitivity-label">
              <div className="prism-sensitivity-label-wrap">
                <PrismDrugNameButton
                  row={row}
                  rowKey={rowKey}
                  isActive={isActive}
                  label={label}
                  onToggle={onDrugToggle}
                />
              </div>
              <span className={`prism-screen-badge prism-screen-badge-${screenType}`}>
                {screenType}
              </span>
            </div>
            <div className="histogram-bar-wrap">
              <div
                className="histogram-bar"
                style={{
                  width: `${(Math.abs(lfc) / maxMagnitude) * 100}%`,
                  background:
                    PRISM_SCREEN_COLORS[screenType] || "var(--accent)",
                }}
              />
            </div>
            <div className="histogram-count" title="Mean log-fold change">
              {lfc.toFixed(2)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DepMapPrismSensitivitySection({ depmap, expanded, onToggle }) {
  const drugSummary = depmap?.drug_sensitivity_summary || [];
  const eligibleCount = depmap?.eligible_cell_lines ?? 0;
  const [activeDrugKey, setActiveDrugKey] = useState("");

  useEffect(() => {
    if (!expanded) {
      setActiveDrugKey("");
    }
  }, [expanded]);

  if (!depmap) {
    return null;
  }

  return (
    <article className="panel panel-wide panel-collapsible panel-prism">
      <button
        type="button"
        className="panel-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="panel-toggle-text">
          <span className="panel-toggle-title">DepMap PRISM drug sensitivity</span>
          <span className="panel-toggle-meta">
            {drugSummary.length.toLocaleString()} compounds across{" "}
            {eligibleCount.toLocaleString()} eligible cell lines
          </span>
        </span>
        <span className="panel-toggle-icon" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded ? (
        <div className="depmap-prism-wrap">
          <p className="depmap-prism-intro">
            Mean PRISM log-fold change across {eligibleCount.toLocaleString()} eligible
            lung cell lines. More negative values indicate stronger growth inhibition.
            Click a drug name for DepMap compound details.
          </p>
          <DepMapPrismSensitivityChart
            drugSummary={drugSummary}
            activeDrugKey={activeDrugKey}
            onDrugToggle={(rowKey) =>
              setActiveDrugKey((current) => (current === rowKey ? "" : rowKey))
            }
          />
          <div className="prism-sensitivity-legend">
            <span className="prism-sensitivity-legend-item">
              <span
                className="prism-sensitivity-legend-swatch"
                style={{ background: PRISM_SCREEN_COLORS.primary }}
              />
              Primary screen
            </span>
            <span className="prism-sensitivity-legend-item">
              <span
                className="prism-sensitivity-legend-swatch"
                style={{ background: PRISM_SCREEN_COLORS.secondary }}
              />
              Secondary screen
            </span>
          </div>
        </div>
      ) : null}
    </article>
  );
}

function ExistingDrugsSection({ existingDrugs, expanded, onToggle, activeDrug, onDrugToggle }) {
  const drugs = buildDrugEntries(existingDrugs?.matched_drugs);
  const selectedDrug = drugs.find((drug) => drug.name === activeDrug);

  return (
    <article className="panel panel-wide panel-collapsible panel-drugs">
      <button
        type="button"
        className="panel-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="panel-toggle-text">
          <span className="panel-toggle-title">Existing drugs</span>
          <span className="panel-toggle-meta">
            {drugs.length.toLocaleString()} drugs from database
          </span>
        </span>
        <span className="panel-toggle-icon" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded ? (
        <div className="drug-list-wrap">
          {drugs.length ? (
            <>
              <ul className="drug-list">
                {drugs.map((drug) => {
                  const isActive = activeDrug === drug.name;
                  return (
                    <li key={drug.name} className="drug-item">
                      <div
                        role="button"
                        tabIndex={0}
                        className={`drug-chip${isActive ? " drug-chip-active" : ""}`}
                        onClick={() => onDrugToggle(drug.name)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            onDrugToggle(drug.name);
                          }
                        }}
                        aria-expanded={isActive}
                        aria-controls={isActive ? "drug-detail-panel" : undefined}
                      >
                        <DrugChipName drug={drug} />
                        <DrugChipBadges drug={drug} />
                      </div>
                    </li>
                  );
                })}
              </ul>
              {selectedDrug ? (
                <div id="drug-detail-panel" className="drug-detail-panel" role="region">
                  <div className="drug-detail-header">
                    <h3 title={selectedDrug.url ? `View CIViC evidence: ${selectedDrug.url}` : undefined}>
                      {selectedDrug.name}
                    </h3>
                    <button
                      type="button"
                      className="drug-detail-close"
                      onClick={() => onDrugToggle(selectedDrug.name)}
                      aria-label={`Close ${selectedDrug.name} details`}
                    >
                      Close
                    </button>
                  </div>
                  {selectedDrug.associations?.length ? (
                    <p className="drug-detail-meta">
                      {selectedDrug.associations.length} association
                      {selectedDrug.associations.length === 1 ? "" : "s"}
                    </p>
                  ) : null}
                  {selectedDrug.associations?.length ? (
                    selectedDrug.associations.map((association, index) => (
                      <div
                        key={`${selectedDrug.name}-${index}`}
                        className="drug-association-card"
                      >
                        <div className="drug-association-badges">
                          {association.mutation_type_badges?.map((badge) => (
                            <span
                              key={`${selectedDrug.name}-${index}-${badge}`}
                              className="drug-badge drug-badge-mutation"
                            >
                              {badge}
                            </span>
                          ))}
                          <span
                            className={`drug-badge drug-badge-response drug-badge-${association.response_category}`}
                          >
                            {formatResponseLabel(association.response_category)}
                          </span>
                          {association.evidence_label ? (
                            <span className="drug-badge drug-badge-evidence">
                              Evidence {association.evidence_label}
                            </span>
                          ) : null}
                        </div>
                        {association.cancer_type ? (
                          <p className="drug-association-meta">{association.cancer_type}</p>
                        ) : null}
                        {association.description ? (
                          <p className="drug-detail-text">{association.description}</p>
                        ) : null}
                        {association.url ? (
                          <p className="drug-detail-links">
                            <a
                              href={association.url}
                              target="_blank"
                              rel="noreferrer"
                              title={association.url}
                            >
                              CIViC evidence link
                            </a>
                          </p>
                        ) : null}
                      </div>
                    ))
                  ) : (
                    <p className="drug-detail-text">
                      No description available for this drug.
                    </p>
                  )}
                </div>
              ) : null}
            </>
          ) : (
            <p className="chart-empty">
              No existing drugs matched these biomarkers in drug database.
            </p>
          )}
        </div>
      ) : null}
    </article>
  );
}

function formatRatingClass(rating) {
  const value = String(rating || "").toLowerCase();
  if (value === "strong") return "rating-strong";
  if (value === "moderate") return "rating-moderate";
  if (value === "challenging") return "rating-challenging";
  if (value === "weak") return "rating-weak";
  return "rating-unknown";
}

function FeasibilitySummaryPanel({ summary }) {
  if (!summary) {
    return null;
  }

  const endpoints = summary.recommended_endpoints || {};
  const secondaryEndpoints = endpoints.secondary_endpoints || [];
  const suggestions = summary.suggestions_to_improve_feasibility || [];

  return (
    <article className="panel panel-wide feasibility-panel">
      <h2>Evidence-based feasibility summary</h2>
      {summary.overall_verdict ? (
        <p className="feasibility-verdict">{summary.overall_verdict}</p>
      ) : null}

      {summary.dimensions?.length ? (
        <>
          <h3 className="feasibility-subtitle">Feasibility dimensions</h3>
          <div className="feasibility-table-wrap">
            <table className="feasibility-table">
              <thead>
                <tr>
                  <th>Dimension</th>
                  <th>Rating</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {summary.dimensions.map((row) => (
                  <tr key={row.dimension}>
                    <td>{row.dimension}</td>
                    <td>
                      <span className={`feasibility-rating ${formatRatingClass(row.rating)}`}>
                        {row.rating || "Unknown"}
                      </span>
                    </td>
                    <td>{row.why || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {endpoints.recommended_phase || endpoints.primary_endpoint ? (
        <>
          <h3 className="feasibility-subtitle">Recommended endpoints</h3>
          <div className="feasibility-table-wrap">
            <table className="feasibility-table feasibility-table-meta">
              <tbody>
                {endpoints.recommended_phase ? (
                  <tr>
                    <th scope="row">Recommended phase</th>
                    <td>{endpoints.recommended_phase}</td>
                  </tr>
                ) : null}
                {endpoints.primary_endpoint ? (
                  <tr>
                    <th scope="row">Primary endpoint</th>
                    <td>{endpoints.primary_endpoint}</td>
                  </tr>
                ) : null}
                {endpoints.primary_rationale ? (
                  <tr>
                    <th scope="row">Primary rationale</th>
                    <td>{endpoints.primary_rationale}</td>
                  </tr>
                ) : null}
                {secondaryEndpoints.length ? (
                  <tr>
                    <th scope="row">Secondary endpoints</th>
                    <td>{secondaryEndpoints.join(", ")}</td>
                  </tr>
                ) : null}
                {endpoints.secondary_rationale ? (
                  <tr>
                    <th scope="row">Secondary rationale</th>
                    <td>{endpoints.secondary_rationale}</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {suggestions.length ? (
        <>
          <h3 className="feasibility-subtitle">Suggestions to improve feasibility</h3>
          <ul className="feasibility-suggestions">
            {suggestions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
    </article>
  );
}

function buildAnalysisSnapshot(result) {
  if (!result) {
    return {};
  }

  return {
    cancer_type: result.eligibility?.cancer_type ?? null,
    required_biomarkers: result.eligibility?.required_biomarkers ?? [],
    excluded_biomarkers: result.eligibility?.excluded_biomarkers ?? [],
    prior_treatments: result.eligibility?.prior_treatments ?? [],
    eligible_patients: result.stats?.eligible_patients ?? null,
    biomarker_eligible_count: result.treatment_stats?.biomarker_eligible_count ?? null,
    prior_treatment_matched_count:
      result.treatment_stats?.prior_treatment_matched_count ?? null,
    overall_verdict: result.feasibility_summary?.overall_verdict ?? null,
    matched_trial_count: result.clinical_trials?.matched_trial_count ?? null,
    matched_drug_count: result.existing_drugs?.matched_drug_count ?? null,
    eligible_cell_lines: result.depmap?.eligible_cell_lines ?? null,
  };
}

function FeedbackPanel({ result }) {
  const [open, setOpen] = useState(false);
  const [rating, setRating] = useState(0);
  const [hoverRating, setHoverRating] = useState(0);
  const [comment, setComment] = useState("");
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [savedAt, setSavedAt] = useState("");

  async function handleFeedbackSubmit(event) {
    event.preventDefault();
    if (rating < 1) {
      setErrorMessage("Please select a star rating.");
      setStatus("error");
      return;
    }

    setStatus("sending");
    setErrorMessage("");

    try {
      const response = await fetch(apiUrl("/api/feedback"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rating,
          comment: comment.trim(),
          email: email.trim() || null,
          page_section: "overall",
          analysis_snapshot: buildAnalysisSnapshot(result),
        }),
      });
      const data = await parseJsonResponse(response);
      if (!response.ok) {
        throw new Error(data.detail || "Feedback submission failed.");
      }
      setSavedAt(data.created_at || "");
      setStatus("sent");
    } catch (err) {
      setErrorMessage(err.message || "Something went wrong.");
      setStatus("error");
    }
  }

  const displayRating = hoverRating || rating;

  return (
    <div className="feedback-float">
      {open ? (
        <div
          id="feedback-panel"
          className="feedback-panel-card"
          role="dialog"
          aria-label="Share feedback"
        >
          <div className="feedback-panel-header">
            <h2>Share feedback</h2>
            <button
              type="button"
              className="feedback-close"
              onClick={() => setOpen(false)}
              aria-label="Close feedback"
            >
              ×
            </button>
          </div>

          {status === "sent" ? (
            <div className="feedback-success">
              <p>
                Thank you — your feedback was saved
                {savedAt ? ` on ${new Date(savedAt).toLocaleString()}` : ""}.
              </p>
            </div>
          ) : (
            <>
              <p className="feedback-panel-intro">
                Help us improve GeneTrail. Your analysis summary is saved with your
                feedback.
              </p>

              <form className="feedback-form" onSubmit={handleFeedbackSubmit}>
                <fieldset className="feedback-fieldset">
                  <legend className="feedback-label">Rating</legend>
                  <div
                    className="star-rating"
                    role="radiogroup"
                    aria-label="Rating from 1 to 5 stars"
                    onMouseLeave={() => setHoverRating(0)}
                  >
                    {[1, 2, 3, 4, 5].map((value) => (
                      <button
                        key={value}
                        type="button"
                        className={
                          value <= displayRating
                            ? "star-button star-button-active"
                            : "star-button"
                        }
                        aria-label={`${value} star${value === 1 ? "" : "s"}`}
                        aria-pressed={rating === value}
                        onMouseEnter={() => setHoverRating(value)}
                        onClick={() => setRating(value)}
                        disabled={status === "sending"}
                      >
                        ★
                      </button>
                    ))}
                  </div>
                </fieldset>

                <label className="feedback-label" htmlFor="feedback-comment">
                  Comment
                </label>
                <textarea
                  id="feedback-comment"
                  className="feedback-textarea"
                  placeholder="What was helpful? What could be better?"
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                  rows={4}
                  maxLength={2000}
                  disabled={status === "sending"}
                />

                <label className="feedback-label" htmlFor="feedback-email">
                  Email <span className="feedback-optional">(optional)</span>
                </label>
                <input
                  id="feedback-email"
                  className="feedback-input"
                  type="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  maxLength={320}
                  disabled={status === "sending"}
                />

                {errorMessage ? <div className="feedback-error">{errorMessage}</div> : null}

                <button
                  type="submit"
                  className="search-button feedback-submit"
                  disabled={status === "sending"}
                >
                  {status === "sending" ? "Sending..." : "Submit feedback"}
                </button>
              </form>
            </>
          )}
        </div>
      ) : null}

      <button
        type="button"
        className="feedback-tab"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="feedback-panel"
      >
        <span className="feedback-tab-icon" aria-hidden="true">
          ★
        </span>
        Feedback
      </button>
    </div>
  );
}

function formatStatusLabel(status) {
  if (!status) {
    return "Unknown";
  }
  return status
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function ClinicalTrialsBarChart({ activeCount, completedCount }) {
  const totalCount = activeCount + completedCount;
  const rows = [
    { label: "Total matched trials", count: totalCount, color: TRIAL_SUMMARY_COLORS.total },
    { label: "Active trials", count: activeCount, color: TRIAL_SUMMARY_COLORS.active },
    {
      label: "Completed / finished trials",
      count: completedCount,
      color: TRIAL_SUMMARY_COLORS.completed,
    },
  ];
  const maxCount = Math.max(...rows.map((row) => row.count), 1);

  if (!totalCount) {
    return <p className="chart-empty">No matched clinical trials found.</p>;
  }

  return (
    <div className="histogram">
      {rows.map((row) => (
        <div className="histogram-row" key={row.label}>
          <div className="histogram-label">{row.label}</div>
          <div className="histogram-bar-wrap">
            <div
              className="histogram-bar"
              style={{
                width: `${(row.count / maxCount) * 100}%`,
                background: row.color,
              }}
            />
          </div>
          <div className="histogram-count">{row.count.toLocaleString()}</div>
        </div>
      ))}
    </div>
  );
}

function CompletedTrialsOutcomePieChart({ outcomeSummary }) {
  const summary = outcomeSummary || {};
  const slices = [
    {
      key: "positive",
      label: "Completed positive",
      count: summary.completed_positive_count || 0,
      color: TRIAL_OUTCOME_COLORS.positive,
    },
    {
      key: "negative",
      label: "Completed negative",
      count: summary.completed_negative_count || 0,
      color: TRIAL_OUTCOME_COLORS.negative,
    },
    {
      key: "failed",
      label: "Failed (stopped early)",
      count: summary.study_stopped_count || 0,
      color: TRIAL_OUTCOME_COLORS.failed,
    },
    {
      key: "inconclusive",
      label: "Completed inconclusive",
      count: summary.completed_inconclusive_count || 0,
      color: TRIAL_OUTCOME_COLORS.inconclusive,
    },
    {
      key: "noResults",
      label: "Completed without results",
      count: summary.completed_no_results_count || 0,
      color: TRIAL_OUTCOME_COLORS.noResults,
    },
  ].filter((slice) => slice.count > 0);

  const total = slices.reduce((sum, slice) => sum + slice.count, 0);
  if (!total) {
    return (
      <p className="chart-empty">No completed clinical trials matched these criteria.</p>
    );
  }

  const normalized = slices.map((slice) => ({
    ...slice,
    percentage: Math.round((1000 * slice.count) / total) / 10,
  }));

  let cumulative = 0;
  const gradientStops = normalized
    .map((slice) => {
      const start = cumulative;
      cumulative += slice.percentage;
      return `${slice.color} ${start}% ${cumulative}%`;
    })
    .join(", ");

  return (
    <div className="pie-chart-layout">
      <div
        className="pie-chart"
        style={{ background: `conic-gradient(${gradientStops})` }}
        aria-hidden="true"
      />
      <ul className="pie-legend">
        {normalized.map((slice) => (
          <li key={slice.key}>
            <span
              className="legend-swatch"
              style={{ background: slice.color }}
            />
            <span>
              {slice.label}: {slice.count.toLocaleString()} ({slice.percentage}%)
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ClinicalTrialSummarySection({
  clinicalTrials,
  completedClinicalTrials,
  expanded,
  onToggle,
}) {
  const activeCount = clinicalTrials?.matched_trial_count ?? 0;
  const completedCount = completedClinicalTrials?.matched_trial_count ?? 0;
  const outcomeSummary = completedClinicalTrials?.outcome_summary;

  return (
    <article className="panel panel-wide panel-collapsible">
      <button
        type="button"
        className="panel-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="panel-toggle-text">
          <span className="panel-toggle-title">Clinical trial summary</span>
          <span className="panel-toggle-meta">
            {(activeCount + completedCount).toLocaleString()} total matched trials from ClinicalTrials.gov
          </span>
        </span>
        <span className="panel-toggle-icon" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded ? (
        <div className="clinical-summary-wrap">
          <div className="charts-grid">
            <section className="chart-panel">
              <h3>Matched trials overview</h3>
              <ClinicalTrialsBarChart
                activeCount={activeCount}
                completedCount={completedCount}
              />
            </section>
            <section className="chart-panel">
              <h3>Completed trial outcomes</h3>
              <CompletedTrialsOutcomePieChart outcomeSummary={outcomeSummary} />
            </section>
          </div>
          {outcomeSummary ? (
            <div className="clinical-summary-stats">
              <StatCard
                label="Completed with results"
                value={outcomeSummary.completed_with_results_count}
                hint="Posted results on ClinicalTrials.gov"
              />
              <StatCard
                label="Primary endpoint met"
                value={outcomeSummary.completed_positive_count}
              />
              <StatCard
                label="Primary endpoint not met"
                value={outcomeSummary.completed_negative_count}
              />
              <StatCard
                label="Failed / stopped"
                value={outcomeSummary.failed_count}
                hint="Stopped early or negative endpoint"
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function outcomeBadgeClass(category) {
  if (category === "completed_positive") {
    return "trial-outcome trial-outcome-positive";
  }
  if (category === "completed_negative") {
    return "trial-outcome trial-outcome-negative";
  }
  if (category === "failed") {
    return "trial-outcome trial-outcome-failed";
  }
  return "trial-outcome";
}

function MatchedClinicalTrialsSection({ clinicalTrials, expanded, onToggle, page, onPageChange }) {
  const trials = clinicalTrials?.matched_trials || [];
  const totalPages = Math.max(1, Math.ceil(trials.length / TRIALS_PER_PAGE));
  const currentPage = Math.min(page, totalPages - 1);
  const pageStart = currentPage * TRIALS_PER_PAGE;
  const pageTrials = trials.slice(pageStart, pageStart + TRIALS_PER_PAGE);

  return (
    <article className="panel panel-wide panel-collapsible">
      <button
        type="button"
        className="panel-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="panel-toggle-text">
          <span className="panel-toggle-title">Matched clinical trials</span>
          <span className="panel-toggle-meta">
            {clinicalTrials.matched_trial_count?.toLocaleString?.() ?? 0} active trials
          </span>
        </span>
        <span className="panel-toggle-icon" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded ? (
        <div className="trial-list-wrap">
          {pageTrials.length ? (
            <ul className="trial-list">
              {pageTrials.map((trial) => (
                <li key={trial.nct_id} className="trial-card">
                  <div className="trial-card-header">
                    <a
                      href={trial.url}
                      target="_blank"
                      rel="noreferrer"
                      className="trial-title-link"
                    >
                      {trial.title || trial.nct_id}
                    </a>
                    <span className="trial-status">{formatStatusLabel(trial.status)}</span>
                  </div>
                  {trial.missing_required_biomarkers?.length ? (
                    <p className="trial-note trial-note-warning">
                      Missing required biomarkers:{" "}
                      {trial.missing_required_biomarkers.join(", ")}
                    </p>
                  ) : null}
                  {trial.conflicting_excluded_biomarkers?.length ? (
                    <p className="trial-note trial-note-warning">
                      Conflicting excluded biomarkers:{" "}
                      {trial.conflicting_excluded_biomarkers.join(", ")}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="chart-empty">No active clinical trials matched these criteria.</p>
          )}

          {trials.length > TRIALS_PER_PAGE ? (
            <div className="trial-pagination">
              <button
                type="button"
                className="ghost-button"
                onClick={() => onPageChange(currentPage - 1)}
                disabled={currentPage === 0}
              >
                Previous
              </button>
              <span className="trial-pagination-meta">
                Page {currentPage + 1} of {totalPages}
              </span>
              <button
                type="button"
                className="ghost-button"
                onClick={() => onPageChange(currentPage + 1)}
                disabled={currentPage >= totalPages - 1}
              >
                Next
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function CompletedClinicalTrialsSection({
  completedClinicalTrials,
  expanded,
  onToggle,
  page,
  onPageChange,
}) {
  const trials = completedClinicalTrials?.matched_trials || [];
  const totalPages = Math.max(1, Math.ceil(trials.length / TRIALS_PER_PAGE));
  const currentPage = Math.min(page, totalPages - 1);
  const pageStart = currentPage * TRIALS_PER_PAGE;
  const pageTrials = trials.slice(pageStart, pageStart + TRIALS_PER_PAGE);

  return (
    <article className="panel panel-wide panel-collapsible">
      <button
        type="button"
        className="panel-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="panel-toggle-text">
          <span className="panel-toggle-title">Completed clinical trials</span>
          <span className="panel-toggle-meta">
            {completedClinicalTrials.matched_trial_count?.toLocaleString?.() ?? 0}{" "}
            finished trials
          </span>
        </span>
        <span className="panel-toggle-icon" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded ? (
        <div className="trial-list-wrap">
          {pageTrials.length ? (
            <ul className="trial-list">
              {pageTrials.map((trial) => (
                <li key={trial.nct_id} className="trial-card">
                  <div className="trial-card-header">
                    <a
                      href={trial.url}
                      target="_blank"
                      rel="noreferrer"
                      className="trial-title-link"
                    >
                      {trial.title || trial.nct_id}
                    </a>
                    <span className={outcomeBadgeClass(trial.outcome_category)}>
                      {trial.outcome_label || formatStatusLabel(trial.status)}
                    </span>
                  </div>
                  {trial.outcome_reason ? (
                    <p className="trial-note">{trial.outcome_reason}</p>
                  ) : null}
                  {trial.missing_required_biomarkers?.length ? (
                    <p className="trial-note trial-note-warning">
                      Missing required biomarkers:{" "}
                      {trial.missing_required_biomarkers.join(", ")}
                    </p>
                  ) : null}
                  {trial.conflicting_excluded_biomarkers?.length ? (
                    <p className="trial-note trial-note-warning">
                      Conflicting excluded biomarkers:{" "}
                      {trial.conflicting_excluded_biomarkers.join(", ")}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="chart-empty">
              No completed clinical trials matched these criteria.
            </p>
          )}

          {trials.length > TRIALS_PER_PAGE ? (
            <div className="trial-pagination">
              <button
                type="button"
                className="ghost-button"
                onClick={() => onPageChange(currentPage - 1)}
                disabled={currentPage === 0}
              >
                Previous
              </button>
              <span className="trial-pagination-meta">
                Page {currentPage + 1} of {totalPages}
              </span>
              <button
                type="button"
                className="ghost-button"
                onClick={() => onPageChange(currentPage + 1)}
                disabled={currentPage >= totalPages - 1}
              >
                Next
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

export default function App() {
  const [protocol, setProtocol] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState(0);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [genePathwayExpanded, setGenePathwayExpanded] = useState(false);
  const [clinicalTrialsExpanded, setClinicalTrialsExpanded] = useState(false);
  const [clinicalTrialsPage, setClinicalTrialsPage] = useState(0);
  const [clinicalTrialSummaryExpanded, setClinicalTrialSummaryExpanded] = useState(true);
  const [trialSitesMapExpanded, setTrialSitesMapExpanded] = useState(true);
  const [completedClinicalTrialsExpanded, setCompletedClinicalTrialsExpanded] =
    useState(false);
  const [completedClinicalTrialsPage, setCompletedClinicalTrialsPage] = useState(0);
  const [existingDrugsExpanded, setExistingDrugsExpanded] = useState(false);
  const [depmapPrismExpanded, setDepmapPrismExpanded] = useState(false);
  const [activeDrug, setActiveDrug] = useState("");
  const [metadataAttribute, setMetadataAttribute] = useState("sex");

  useEffect(() => {
    if (!loading) {
      return undefined;
    }

    setLoadingStep(0);
    const interval = window.setInterval(() => {
      setLoadingStep((current) =>
        current < ANALYSIS_STEPS.length - 1 ? current + 1 : current
      );
    }, 2200);

    return () => window.clearInterval(interval);
  }, [loading]);

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResult(null);
    setGenePathwayExpanded(false);
    setClinicalTrialsExpanded(false);
    setClinicalTrialsPage(0);
    setClinicalTrialSummaryExpanded(true);
    setTrialSitesMapExpanded(true);
    setCompletedClinicalTrialsExpanded(false);
    setCompletedClinicalTrialsPage(0);
    setExistingDrugsExpanded(false);
    setDepmapPrismExpanded(false);
    setActiveDrug("");
    setMetadataAttribute("sex");

    const text = protocol.trim();
    if (text.length < 10) {
      setError("Please enter at least a few lines of protocol text.");
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(apiUrl("/api/analyze"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ protocol: text }),
      });

      const data = await parseJsonResponse(response);
      if (!response.ok) {
        throw new Error(data.detail || "Analysis failed.");
      }
      setLoadingStep(ANALYSIS_STEPS.length);
      setResult(data);
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setLoading(false);
      setLoadingStep(0);
    }
  }

  return (
    <div className="page">
      <header className="hero">
        <div className="brand">GeneTrail</div>
        <p className="tagline">
          Paste a clinical trial protocol to extract eligibility and estimate
          matching NSCLC patients.
        </p>
      </header>

      <main className="search-shell">
        <form className="search-form" onSubmit={handleSubmit}>
          <label className="sr-only" htmlFor="protocol">
            Protocol text
          </label>
          <textarea
            id="protocol"
            className="protocol-input"
            placeholder="Paste trial eligibility criteria here..."
            value={protocol}
            onChange={(event) => setProtocol(event.target.value)}
            rows={7}
            disabled={loading}
          />

          <div className="actions">
            <button type="submit" className="search-button" disabled={loading}>
              {loading ? "Analyzing..." : "Analyze protocol"}
            </button>
            <button
              type="button"
              className="ghost-button"
              onClick={() => setProtocol(EXAMPLE_PROTOCOL)}
              disabled={loading}
            >
              Use example
            </button>
          </div>
        </form>

        {loading ? (
          <AnalysisProgressPanel steps={ANALYSIS_STEPS} activeStep={loadingStep} />
        ) : null}

        {error ? <div className="error-banner">{error}</div> : null}

        {result ? (
          <section className="results">
            {result.feasibility_summary ? (
              <FeasibilitySummaryPanel summary={result.feasibility_summary} />
            ) : null}

            <div className="results-grid">
              <article className="panel">
                <h2>Extracted eligibility</h2>
                <dl className="detail-list">
                  <div>
                    <dt>Cancer type</dt>
                    <dd>{result.eligibility.cancer_type || "Not specified"}</dd>
                  </div>
                  <div>
                    <dt>Stage</dt>
                    <dd>{result.eligibility.stage || "Not specified"}</dd>
                  </div>
                  <div>
                    <dt>Required biomarkers</dt>
                    <dd>{formatList(result.eligibility.required_biomarkers)}</dd>
                  </div>
                  <div>
                    <dt>Excluded biomarkers</dt>
                    <dd>{formatList(result.eligibility.excluded_biomarkers)}</dd>
                  </div>
                  <div>
                    <dt>Prior treatments</dt>
                    <dd>{formatList(result.eligibility.prior_treatments)}</dd>
                  </div>
                  <div>
                    <dt>ECOG status</dt>
                    <dd>{result.eligibility.ecog_status || "Not specified"}</dd>
                  </div>
                </dl>
              </article>

              <article className="panel">
                <h2>Patient match summary</h2>
                <div className="stats-grid">
                  <StatCard
                    label="NSCLC patients"
                    value={result.stats.unique_patients_with_cancer_type}
                    hint={`${result.stats.studies_searched} studies in Database`}
                  />
                  <StatCard
                    label="With required biomarkers"
                    value={result.stats.patients_with_required_biomarkers}
                  />
                  <StatCard
                    label="With matched biomarker"
                    value={result.stats.eligible_patients}
                    hint="excluded biomarkers absent"
                  />
                  <StatCard
                    label="Eligible patients"
                    value={
                      result.treatment_stats?.prior_treatment_matched_count ??
                      result.stats.eligible_patients
                    }
                    hint="both biomarker and prior treatment matched"
                  />
                </div>
              </article>

              {result.depmap ? (
                <article className="panel">
                  <h2>Cell line match summary</h2>
                  <div className="stats-grid">
                    <StatCard
                      label="Lung cell lines"
                      value={result.depmap.total_cell_lines}
                      hint="NSCLC subset in DepMap"
                    />
                    <StatCard
                      label="With required biomarkers"
                      value={result.depmap.cell_lines_with_required_biomarkers}
                    />
                    <StatCard
                      label="Eligible cell lines"
                      value={result.depmap.eligible_cell_lines}
                      hint="excluded biomarkers absent"
                    />
                  </div>
                </article>
              ) : null}
            </div>

            {result.stats.gene_patient_counts?.length || result.depmap?.gene_effect_summary?.length ? (
              <GeneMolecularPathwaySection
                genePatientCounts={result.stats.gene_patient_counts}
                depmap={result.depmap}
                eligibility={result.eligibility}
                expanded={genePathwayExpanded}
                onToggle={() => setGenePathwayExpanded((value) => !value)}
              />
            ) : null}

            {result.clinical_trials || result.completed_clinical_trials ? (
              <ClinicalTrialSummarySection
                clinicalTrials={result.clinical_trials}
                completedClinicalTrials={result.completed_clinical_trials}
                expanded={clinicalTrialSummaryExpanded}
                onToggle={() => setClinicalTrialSummaryExpanded((value) => !value)}
              />
            ) : null}

            {result.trial_sites ? (
              <TrialSitesMapSection
                trialSites={result.trial_sites}
                expanded={trialSitesMapExpanded}
                onToggle={() => setTrialSitesMapExpanded((value) => !value)}
              />
            ) : null}

            {result.clinical_trials ? (
              <MatchedClinicalTrialsSection
                clinicalTrials={result.clinical_trials}
                expanded={clinicalTrialsExpanded}
                onToggle={() => setClinicalTrialsExpanded((value) => !value)}
                page={clinicalTrialsPage}
                onPageChange={setClinicalTrialsPage}
              />
            ) : null}

            {result.completed_clinical_trials ? (
              <CompletedClinicalTrialsSection
                completedClinicalTrials={result.completed_clinical_trials}
                expanded={completedClinicalTrialsExpanded}
                onToggle={() =>
                  setCompletedClinicalTrialsExpanded((value) => !value)
                }
                page={completedClinicalTrialsPage}
                onPageChange={setCompletedClinicalTrialsPage}
              />
            ) : null}

            {result.existing_drugs ? (
              <ExistingDrugsSection
                existingDrugs={result.existing_drugs}
                expanded={existingDrugsExpanded}
                onToggle={() => {
                  setExistingDrugsExpanded((value) => !value);
                  setActiveDrug("");
                }}
                activeDrug={activeDrug}
                onDrugToggle={(drugName) =>
                  setActiveDrug((current) => (current === drugName ? "" : drugName))
                }
              />
            ) : null}

            {result.depmap ? (
              <DepMapPrismSensitivitySection
                depmap={result.depmap}
                expanded={depmapPrismExpanded}
                onToggle={() => setDepmapPrismExpanded((value) => !value)}
              />
            ) : null}

            {result.treatment_stats ? (
              <article className="panel panel-wide">
                <h2>Treatment and survival outcomes</h2>

                {result.control_stats ? (
                  <div className="control-benchmark">
                    <p className="control-benchmark-title">NSCLC control benchmark</p>
                    <ControlBenchmarkStackedChart controlStats={result.control_stats} />
                  </div>
                ) : null}

                <div className="charts-grid">
                  <section className="chart-panel">
                    <h3>Matched cohort survival status</h3>
                    <OsStatusPieChart
                      distribution={result.treatment_stats.os_status_distribution}
                    />
                  </section>
                  <section className="chart-panel">
                    <h3>Matched cohort survival days</h3>
                    <OsDaysHistogram
                      distribution={result.treatment_stats.os_days_distribution}
                    />
                  </section>
                </div>
              </article>
            ) : null}

            {result.patient_metadata_stats ? (
              <article className="panel panel-wide">
                <h2>Patient attributes by survival status</h2>
                <p className="metadata-panel-intro">
                  Matched cohort demographic and clinical attributes split by overall
                  survival status.
                </p>
                <PatientAttributesByOsStatusChart
                  metadataStats={result.patient_metadata_stats}
                  activeAttr={metadataAttribute}
                  onAttrChange={setMetadataAttribute}
                />
              </article>
            ) : null}
          </section>
        ) : null}
      </main>

      {result ? <FeedbackPanel result={result} /> : null}
    </div>
  );
}
