# PostHog post-wizard report

The wizard has completed a deep integration of PostHog analytics into GeneTrail AI, a FastAPI-based oncology clinical trial feasibility tool. PostHog is initialized at application startup using the `Posthog()` class constructor inside a FastAPI `lifespan` context manager, and is shut down cleanly on exit. Four server-side events are captured across the two primary API endpoints (`/api/analyze` and `/api/feedback`), tracking the full protocol analysis lifecycle — submission, completion, failure, and user feedback.

| Event Name | Description | File |
|---|---|---|
| `protocol_analyzed` | User submits a clinical trial protocol text for analysis | `api.py` |
| `analysis_completed` | Protocol analysis pipeline completes successfully with feasibility results | `api.py` |
| `analysis_failed` | Protocol analysis pipeline fails due to a runtime or unexpected error | `api.py` |
| `feedback_submitted` | User submits a star rating and optional comment via the feedback form | `api.py` |
| `$pageview` | User loads the GeneTrail app | `frontend/src/posthog.js` |
| `ui_click` | User clicks buttons, links, tabs, or panel toggles | `frontend/src/posthog.js` |
| `analyze_clicked` | User submits the analyze form | `frontend/src/App.jsx` |
| `analyze_succeeded` | Frontend receives a successful analyze response | `frontend/src/App.jsx` |
| `analyze_failed` | Frontend analyze request fails | `frontend/src/App.jsx` |

## Next steps

We've built some insights and a dashboard for you to keep an eye on user behavior, based on the events we just instrumented:

- [Analytics basics (wizard) Dashboard](https://us.posthog.com/project/476834/dashboard/1733402)
- [Protocol Analyses Over Time](https://us.posthog.com/project/476834/insights/DOidGZG6)
- [Analysis Completions vs Failures](https://us.posthog.com/project/476834/insights/6Zz1PTEA)
- [Feedback Submissions](https://us.posthog.com/project/476834/insights/oa77ta9c)
- [Total Analyses (Last 30 Days)](https://us.posthog.com/project/476834/insights/oImZEGyc)
- [Analysis Success Rate](https://us.posthog.com/project/476834/insights/3cmIUfk1)

## Verify before merging

- [ ] Run a full production build (the wizard only verified the files it touched) and fix any lint or type errors introduced by the generated code.
- [ ] Run the test suite — call sites that were rewritten or instrumented may need updated mocks or fixtures.
- [ ] Add `POSTHOG_PROJECT_TOKEN` and `POSTHOG_HOST` to Railway and `.env.example`.
- [ ] Add `VITE_POSTHOG_KEY` and `VITE_POSTHOG_HOST` to Vercel and GitHub deploy secrets.

### Agent skill

We've left an agent skill folder in your project. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.
