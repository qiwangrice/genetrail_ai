import { useEffect, useState } from "react";
import "./App.css";

const ANALYSIS_STEPS = [
  "Extracting eligibility criteria",
  "Searching NSCLC patient cohort",
  "Matching treatments and survival",
  "Searching active and completed trials",
  "Looking up existing therapies",
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
  const tooltip = drug.url ? `View VICC evidence: ${drug.url}` : undefined;

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
            {drugs.length.toLocaleString()} drugs from VICC
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
                    <h3 title={selectedDrug.url ? `View VICC evidence: ${selectedDrug.url}` : undefined}>
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
                              VICC evidence link
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
              No existing drugs matched these biomarkers in VICC.
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
  const [geneCountsExpanded, setGeneCountsExpanded] = useState(false);
  const [clinicalTrialsExpanded, setClinicalTrialsExpanded] = useState(false);
  const [clinicalTrialsPage, setClinicalTrialsPage] = useState(0);
  const [clinicalTrialSummaryExpanded, setClinicalTrialSummaryExpanded] = useState(true);
  const [completedClinicalTrialsExpanded, setCompletedClinicalTrialsExpanded] =
    useState(false);
  const [completedClinicalTrialsPage, setCompletedClinicalTrialsPage] = useState(0);
  const [existingDrugsExpanded, setExistingDrugsExpanded] = useState(false);
  const [activeDrug, setActiveDrug] = useState("");

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
    setGeneCountsExpanded(false);
    setClinicalTrialsExpanded(false);
    setClinicalTrialsPage(0);
    setClinicalTrialSummaryExpanded(true);
    setCompletedClinicalTrialsExpanded(false);
    setCompletedClinicalTrialsPage(0);
    setExistingDrugsExpanded(false);
    setActiveDrug("");

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
                    label="Eligible patients"
                    value={result.stats.eligible_patients}
                    hint="Required present, excluded absent"
                  />
                </div>
              </article>
            </div>

            {result.stats.gene_patient_counts?.length ? (
              <article className="panel panel-wide panel-collapsible">
                <button
                  type="button"
                  className="panel-toggle"
                  onClick={() => setGeneCountsExpanded((expanded) => !expanded)}
                  aria-expanded={geneCountsExpanded}
                >
                  <span className="panel-toggle-text">
                    <span className="panel-toggle-title">Gene-level patient counts</span>
                    <span className="panel-toggle-meta">
                      {result.stats.gene_patient_counts.length} genes from cBioPortal
                    </span>
                  </span>
                  <span className="panel-toggle-icon" aria-hidden="true">
                    {geneCountsExpanded ? "−" : "+"}
                  </span>
                </button>
                {geneCountsExpanded ? (
                  <div className="gene-table-wrap">
                    <table className="gene-table">
                      <thead>
                        <tr>
                          <th>Gene</th>
                          <th>With mutation</th>
                          <th>Without mutation</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.stats.gene_patient_counts.map((row) => (
                          <tr key={row.gene}>
                            <td>{row.gene}</td>
                            <td>{row.patients_with_mutation.toLocaleString()}</td>
                            <td>{row.patients_without_mutation.toLocaleString()}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </article>
            ) : null}

            {result.clinical_trials || result.completed_clinical_trials ? (
              <ClinicalTrialSummarySection
                clinicalTrials={result.clinical_trials}
                completedClinicalTrials={result.completed_clinical_trials}
                expanded={clinicalTrialSummaryExpanded}
                onToggle={() => setClinicalTrialSummaryExpanded((value) => !value)}
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
          </section>
        ) : null}
      </main>
    </div>
  );
}
