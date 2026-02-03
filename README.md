# GovGraph (MVP)

GovGraph is a small, API-first product that turns **hard-to-use public-sector procurement and contractor data** into **clean, joinable JSON** with **webhook alerts**.

## Target customer (MVP)
**GovTech and compliance product teams** who need to embed procurement intelligence (contractors, awards, opportunities) into their own software without building a bespoke ingestion pipeline.

## What it wraps (MVP)
- **SAM.gov opportunities** (search + polling for “new opportunity” webhooks)
- **SAM.gov entity + exclusions** (best-effort; requires keys/onboarding)
- **USAspending.gov** (award search/summary; best-effort)

Endpoints are configurable because upstream APIs evolve.

## Required upstream keys
- For most SAM.gov endpoints you’ll need an `api.data.gov` key. Set it as `GOVGRAPH_API_DATA_GOV_KEY` in `.env`, then restart GovGraph.

## What you get
- A single **“contractor profile”** response that joins multiple sources by UEI.
- A normalized **opportunity search** endpoint (and a webhook stream for new opportunities).
- Built-in **provenance** fields (source URLs + timestamps) so customers can audit.

## Suggested packaging (go-to-market)
- **Developer (free/low-cost):** limited rate, no SLA, basic search + contractor profile.
- **Pro ($99–$499/mo):** higher quotas, webhooks, history retention, and export endpoints.
- **Enterprise (custom):** SSO, audit logs, dedicated connectors (FPDS/state portals), and SLAs.

## Roadmap (next streams to add)
- **FPDS modernization:** ingest legacy procurement history and expose normalized award records via REST.
- **State/local business & permit data:** connector framework + per-jurisdiction adapters (start with top metros/states).

## Quickstart
1) Create a local env file:
```bash
cp .env.example .env
```

2) Run the API:
```bash
PYTHONPATH=src python -m uvicorn govgraph.main:app --reload
```

If you see `Address already in use`, pick another port:
```bash
PYTHONPATH=src python -m uvicorn govgraph.main:app --reload --port 8001
```

3) Health check:
```bash
curl -s http://127.0.0.1:8000/healthz | python -m json.tool
```

4) Open the frontend console:
- http://127.0.0.1:8000/

5) Try an opportunity search (requires upstream connectivity + a key depending on SAM config):
```bash
curl -s "http://127.0.0.1:8000/v1/opportunities/search?q=software&limit=10" | python -m json.tool
```

6) Create a webhook subscription (GovGraph will POST events to your URL when enabled):
```bash
curl -s -X POST http://127.0.0.1:8000/v1/webhooks/subscriptions \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/webhook","event_type":"sam.opportunity.created","filters":{"q":"software"}}' \
  | python -m json.tool
```

## Notes / disclaimers
- This MVP is **not legal advice** and does not guarantee completeness of upstream data.
- Respect upstream terms, quotas, and politeness policies. Production deployments should add:
  - durable queues, retries, DLQs
  - per-source rate limiting and caching
  - stronger auth, tenant isolation, and observability
