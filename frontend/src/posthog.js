import posthog from "posthog-js";

let initialized = false;

export function initPostHog() {
  const key = import.meta.env.POSTHOG_PROJECT_TOKEN;
  const host = import.meta.env.POSTHOG_HOST || "https://us.i.posthog.com";

  if (!key || initialized) {
    return Boolean(key);
  }

  posthog.init(key, {
    api_host: host,
    person_profiles: "identified_only",
    capture_pageview: true,
    capture_pageleave: true,
    autocapture: false,
  });

  initialized = true;
  return true;
}

export function isPostHogEnabled() {
  return initialized && Boolean(import.meta.env.POSTHOG_PROJECT_TOKEN);
}

export function trackPageView() {
  if (!isPostHogEnabled()) {
    return;
  }

  posthog.capture("$pageview", {
    $current_url: window.location.href,
    $pathname: window.location.pathname,
    $host: window.location.host,
  });
}

function describeClickTarget(target) {
  if (!(target instanceof Element)) {
    return {};
  }

  const trackLabel = target.dataset?.track || target.closest("[data-track]")?.dataset?.track;
  const panelTitle = target.closest(".panel-toggle")?.querySelector(".panel-toggle-title")
    ?.textContent;

  return {
    element_tag: target.tagName.toLowerCase(),
    element_id: target.id || null,
    element_type: target.getAttribute("type") || null,
    element_role: target.getAttribute("role") || null,
    element_class: String(target.className || "").slice(0, 160) || null,
    element_text: (target.textContent || "").trim().slice(0, 100) || null,
    data_track: trackLabel || null,
    panel_title: panelTitle?.trim() || null,
  };
}

export function setupClickTracking() {
  if (!isPostHogEnabled()) {
    return undefined;
  }

  function onClick(event) {
    const target = event.target.closest(
      "button, a, [role='tab'], .panel-toggle, input[type='submit'], .search-button, .ghost-button, .attribute-tab, .prism-drug-name-btn"
    );

    if (!target) {
      return;
    }

    posthog.capture("ui_click", {
      ...describeClickTarget(target),
      client_x: event.clientX,
      client_y: event.clientY,
      page_x: event.pageX,
      page_y: event.pageY,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      scroll_y: window.scrollY,
      $pathname: window.location.pathname,
    });
  }

  document.addEventListener("click", onClick, true);
  return () => document.removeEventListener("click", onClick, true);
}

export function posthogRequestHeaders() {
  if (!isPostHogEnabled()) {
    return {};
  }

  const headers = {
    "X-PostHog-Distinct-Id": posthog.get_distinct_id(),
  };
  const sessionId = posthog.get_session_id?.();
  if (sessionId) {
    headers["X-PostHog-Session-Id"] = sessionId;
  }
  return headers;
}

export function trackEvent(eventName, properties = {}) {
  if (!isPostHogEnabled()) {
    return;
  }
  posthog.capture(eventName, properties);
}
