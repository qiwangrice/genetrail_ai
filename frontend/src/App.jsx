import { useState } from "react";
import "./App.css";

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

function ControlBenchmarkCard({
  title,
  patientCount,
  livingPercentage,
  averageSurvivalDays,
}) {
  return (
    <div className="control-card">
      <h4>{title}</h4>
      <dl className="control-metrics">
        <div>
          <dt>Patients</dt>
          <dd>{patientCount?.toLocaleString?.() ?? "N/A"}</dd>
        </div>
        <div>
          <dt>Living</dt>
          <dd>{livingPercentage != null ? `${livingPercentage}%` : "N/A"}</dd>
        </div>
        <div>
          <dt>Avg survival days</dt>
          <dd>
            {averageSurvivalDays != null
              ? `${averageSurvivalDays.toLocaleString()} days`
              : "N/A"}
          </dd>
        </div>
      </dl>
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

function buildDrugEntries(matchedDrugs) {
  const byName = new Map();

  for (const treatment of matchedDrugs || []) {
    for (const drug of treatment.drugs || []) {
      const name = drug.drug_name;
      if (!name) {
        continue;
      }

      if (!byName.has(name)) {
        byName.set(name, {
          name,
          ncitCode: drug.ncit_code || null,
          descriptions: [],
          levels: new Set(),
        });
      }

      const entry = byName.get(name);
      if (treatment.description && !entry.descriptions.includes(treatment.description)) {
        entry.descriptions.push(treatment.description);
      }
      if (treatment.level) {
        entry.levels.add(treatment.level);
      }
    }
  }

  return [...byName.values()]
    .map((entry) => ({
      ...entry,
      levels: [...entry.levels],
    }))
    .sort((left, right) => left.name.localeCompare(right.name));
}

function ExistingDrugsSection({ existingDrugs, expanded, onToggle, activeDrug, onDrugToggle }) {
  const drugs = buildDrugEntries(existingDrugs?.matched_drugs);

  return (
    <article className="panel panel-wide panel-collapsible">
      <button
        type="button"
        className="panel-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="panel-toggle-text">
          <span className="panel-toggle-title">Existing drugs</span>
          <span className="panel-toggle-meta">
            {drugs.length.toLocaleString()} drugs from OncoKB
          </span>
        </span>
        <span className="panel-toggle-icon" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded ? (
        <div className="drug-list-wrap">
          {drugs.length ? (
            <ul className="drug-list">
              {drugs.map((drug) => {
                const isActive = activeDrug === drug.name;
                return (
                  <li key={drug.name} className="drug-item">
                    <button
                      type="button"
                      className={`drug-chip${isActive ? " drug-chip-active" : ""}`}
                      onClick={() => onDrugToggle(drug.name)}
                      aria-expanded={isActive}
                      aria-describedby={isActive ? `drug-tooltip-${drug.name}` : undefined}
                    >
                      {drug.name}
                    </button>
                    {isActive ? (
                      <div
                        id={`drug-tooltip-${drug.name}`}
                        className="drug-tooltip"
                        role="tooltip"
                      >
                        {drug.levels.length ? (
                          <p className="drug-tooltip-meta">
                            OncoKB level: {drug.levels.join(", ")}
                          </p>
                        ) : null}
                        {drug.descriptions.length ? (
                          drug.descriptions.map((description) => (
                            <p key={description.slice(0, 40)} className="drug-tooltip-text">
                              {description}
                            </p>
                          ))
                        ) : (
                          <p className="drug-tooltip-text">
                            No description available for this drug.
                          </p>
                        )}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="chart-empty">
              No existing drugs matched these biomarkers in OncoKB.
            </p>
          )}
        </div>
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

export default function App() {
  const [protocol, setProtocol] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [geneCountsExpanded, setGeneCountsExpanded] = useState(false);
  const [clinicalTrialsExpanded, setClinicalTrialsExpanded] = useState(false);
  const [clinicalTrialsPage, setClinicalTrialsPage] = useState(0);
  const [existingDrugsExpanded, setExistingDrugsExpanded] = useState(false);
  const [activeDrug, setActiveDrug] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResult(null);
    setGeneCountsExpanded(false);
    setClinicalTrialsExpanded(false);
    setClinicalTrialsPage(0);
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
      setResult(data);
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setLoading(false);
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

        {error ? <div className="error-banner">{error}</div> : null}

        {result ? (
          <section className="results">
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
                    hint={`${result.stats.studies_searched} studies in Neon`}
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
                      {result.stats.gene_patient_counts.length} genes
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

            {result.clinical_trials ? (
              <MatchedClinicalTrialsSection
                clinicalTrials={result.clinical_trials}
                expanded={clinicalTrialsExpanded}
                onToggle={() => setClinicalTrialsExpanded((value) => !value)}
                page={clinicalTrialsPage}
                onPageChange={setClinicalTrialsPage}
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
                    <p className="control-benchmark-title">
                      NSCLC control benchmark
                    </p>
                    <div className="control-benchmark-grid">
                      <ControlBenchmarkCard
                        title="With treatment"
                        patientCount={
                          result.control_stats.with_treatment.patient_count
                        }
                        livingPercentage={
                          result.control_stats.with_treatment.living_percentage
                        }
                        averageSurvivalDays={
                          result.control_stats.with_treatment.average_survival_days
                        }
                      />
                      <ControlBenchmarkCard
                        title="Without treatment"
                        patientCount={
                          result.control_stats.without_treatment.patient_count
                        }
                        livingPercentage={
                          result.control_stats.without_treatment.living_percentage
                        }
                        averageSurvivalDays={
                          result.control_stats.without_treatment.average_survival_days
                        }
                      />
                    </div>
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
