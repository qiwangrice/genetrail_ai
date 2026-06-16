import { useEffect, useMemo } from "react";
import L from "leaflet";
import {
  MapContainer,
  Marker,
  Popup,
  TileLayer,
  useMap,
} from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";

import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";

const ACTIVE_TRIAL_STATUSES = new Set([
  "RECRUITING",
  "NOT_YET_RECRUITING",
  "ENROLLING_BY_INVITATION",
  "ACTIVE_NOT_RECRUITING",
]);

const DEFAULT_MAP_CENTER = [20, 0];
const DEFAULT_MAP_ZOOM = 2;

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

export function siteMarkerKey(site) {
  return [site.site_name, site.city, site.state, site.country]
    .map((value) => String(value || "").trim().toLowerCase())
    .join("|");
}

function siteMetricValue(site) {
  return (
    site.total_patients_enrolled ??
    site.total_enrolled ??
    site.trial_count ??
    0
  );
}

function trialSiteMarkerRadius(site, maxMetric) {
  const minRadius = 7;
  const maxRadius = 24;
  const metric = siteMetricValue(site);
  if (!metric || !maxMetric) {
    return minRadius;
  }
  return minRadius + (maxRadius - minRadius) * Math.sqrt(metric / maxMetric);
}

function formatSiteLocation(site) {
  return [site.site_name, site.city, site.state, site.country]
    .filter(Boolean)
    .join(", ");
}

function createSiteIcon(site, maxMetric) {
  const radius = trialSiteMarkerRadius(site, maxMetric);
  const diameter = radius * 2;
  return L.divIcon({
    className: "trial-site-leaflet-marker-wrap",
    html: `<span class="trial-site-leaflet-marker" style="width:${diameter}px;height:${diameter}px"></span>`,
    iconSize: [diameter, diameter],
    iconAnchor: [radius, radius],
    popupAnchor: [0, -radius],
  });
}

function TrialSitePopupContent({ site }) {
  const trials = site?.trials || [];
  const locationLabel = formatSiteLocation(site || {});

  return (
    <div className="trial-site-popup">
      <div className="trial-site-tooltip-header">
        <div>
          <p className="trial-site-tooltip-title">
            {site?.site_name || site?.city || "Trial site"}
          </p>
          {locationLabel ? (
            <p className="trial-site-tooltip-subtitle">{locationLabel}</p>
          ) : null}
        </div>
      </div>

      <dl className="trial-site-tooltip-meta">
        <div>
          <dt>Matched trials</dt>
          <dd>{site?.trial_count?.toLocaleString?.() ?? 0}</dd>
        </div>
        <div>
          <dt>Active trials</dt>
          <dd>{site?.active_trial_count?.toLocaleString?.() ?? 0}</dd>
        </div>
        <div>
          <dt>Completed / finished trials</dt>
          <dd>{site?.completed_trial_count?.toLocaleString?.() ?? 0}</dd>
        </div>
        <div>
          <dt>Treatment arm enrolled</dt>
          <dd>
            {site?.total_patients_enrolled != null
              ? site.total_patients_enrolled.toLocaleString()
              : "Not reported"}
          </dd>
        </div>
        <div>
          <dt>Control arm enrolled</dt>
          <dd>
            {site?.total_control_enrolled != null
              ? site.total_control_enrolled.toLocaleString()
              : "Not reported"}
          </dd>
        </div>
      </dl>

      {trials.length ? (
        <div className="trial-site-tooltip-trials">
          <p className="trial-site-tooltip-trials-title">Linked trials</p>
          <ul>
            {trials.map((trial) => (
              <li key={trial.nct_id}>
                <div className="trial-site-tooltip-trial-head">
                  {trial.trial_url ? (
                    <a href={trial.trial_url} target="_blank" rel="noreferrer">
                      {trial.nct_id}
                    </a>
                  ) : (
                    <span>{trial.nct_id}</span>
                  )}
                  <span
                    className={
                      ACTIVE_TRIAL_STATUSES.has(
                        String(trial.trial_status || "").toUpperCase()
                      )
                        ? "trial-site-status trial-site-status-active"
                        : "trial-site-status trial-site-status-finished"
                    }
                  >
                    {formatStatusLabel(trial.trial_status)}
                  </span>
                </div>
                <p className="trial-site-tooltip-trial-title">{trial.trial_title}</p>
                <p className="trial-site-tooltip-trial-meta">
                  Site status: {formatStatusLabel(trial.site_status) || "Unknown"}
                  {trial.patients_enrolled != null
                    ? ` · Treatment: ${Number(trial.patients_enrolled).toLocaleString()}`
                    : ""}
                  {trial.control_enrolled != null
                    ? ` · Control: ${Number(trial.control_enrolled).toLocaleString()}`
                    : ""}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {trials[0]?.enrollment_note ? (
        <p className="trial-site-tooltip-note">{trials[0].enrollment_note}</p>
      ) : null}
    </div>
  );
}

function FitBoundsToSites({ sites }) {
  const map = useMap();

  useEffect(() => {
    if (!sites.length) {
      map.setView(DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM);
      return;
    }

    const bounds = L.latLngBounds(
      sites.map((site) => [site.latitude, site.longitude])
    );
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 8 });
  }, [map, sites]);

  return null;
}

function InvalidateMapSize({ active }) {
  const map = useMap();

  useEffect(() => {
    if (!active) {
      return undefined;
    }

    const frame = window.requestAnimationFrame(() => {
      map.invalidateSize();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, map]);

  return null;
}

function TrialSitesLeafletMap({ sites, maxMetric }) {
  return (
    <MapContainer
      className="trial-sites-leaflet-map"
      center={DEFAULT_MAP_CENTER}
      zoom={DEFAULT_MAP_ZOOM}
      scrollWheelZoom
      minZoom={2}
      maxZoom={12}
      worldCopyJump
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
      />
      <FitBoundsToSites sites={sites} />
      <InvalidateMapSize active />

      <MarkerClusterGroup
        chunkedLoading
        showCoverageOnHover={false}
        spiderfyOnMaxZoom
        zoomToBoundsOnClick
        maxClusterRadius={50}
      >
        {sites.map((site) => (
          <Marker
            key={siteMarkerKey(site)}
            position={[site.latitude, site.longitude]}
            icon={createSiteIcon(site, maxMetric)}
          >
            <Popup className="trial-site-leaflet-popup" maxWidth={420} minWidth={280}>
              <TrialSitePopupContent site={site} />
            </Popup>
          </Marker>
        ))}
      </MarkerClusterGroup>
    </MapContainer>
  );
}

export function TrialSitesMapSection({ trialSites, expanded, onToggle }) {
  const uniqueSites = trialSites?.unique_sites || [];
  const mapSites = useMemo(
    () =>
      uniqueSites.filter(
        (site) =>
          Number.isFinite(site.latitude) && Number.isFinite(site.longitude)
      ),
    [uniqueSites]
  );
  const maxMetric = useMemo(
    () => Math.max(...mapSites.map((site) => siteMetricValue(site)), 1),
    [mapSites]
  );

  const mappedCount = mapSites.length;
  const totalUnique = trialSites?.unique_site_count ?? uniqueSites.length;

  return (
    <article className="panel panel-wide panel-collapsible">
      <button
        type="button"
        className="panel-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span className="panel-toggle-text">
          <span className="panel-toggle-title">Trial site map</span>
          <span className="panel-toggle-meta">
            {mappedCount.toLocaleString()} mapped sites
            {totalUnique ? ` of ${totalUnique.toLocaleString()} unique sites` : ""}
          </span>
        </span>
        <span className="panel-toggle-icon" aria-hidden="true">
          {expanded ? "−" : "+"}
        </span>
      </button>

      {expanded ? (
        <div className="trial-sites-map-wrap">
          <p className="trial-sites-map-intro">
            Interactive map of matched trial locations from ClinicalTrials.gov. Cluster
            markers zoom into regions; dot size reflects treatment-arm enrollment across
            linked trials (trial-level counts).
          </p>

          {!mappedCount ? (
            <p className="chart-empty">
              No geocoded trial sites were returned for this protocol.
            </p>
          ) : null}

          <div className="trial-sites-map-legend">
            <span className="trial-sites-map-legend-dot" aria-hidden="true" />
            <span>Trial site location</span>
            <span className="trial-sites-map-legend-sep">·</span>
            <span>Larger dot = more enrolled patients</span>
            <span className="trial-sites-map-legend-sep">·</span>
            <span>Click cluster to zoom in</span>
          </div>

          <div className="trial-sites-map-shell">
            {mappedCount ? (
              <TrialSitesLeafletMap sites={mapSites} maxMetric={maxMetric} />
            ) : (
              <TrialSitesLeafletMap sites={[]} maxMetric={1} />
            )}
          </div>
        </div>
      ) : null}
    </article>
  );
}
