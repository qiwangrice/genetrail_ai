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

export default function App() {
  const [protocol, setProtocol] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResult(null);

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
              <article className="panel panel-wide">
                <h2>Gene-level patient counts</h2>
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
              </article>
            ) : null}
          </section>
        ) : null}
      </main>
    </div>
  );
}
