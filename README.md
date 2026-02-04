# GovGraph

One HTTP service over the federal procurement APIs. Clean JSON from SAM.gov and USAspending.gov, plus a signed webhook when a new opportunity matches a saved search.

A contractor's federal record is split across systems that don't share keys: SAM.gov has the registration, exclusions, and open solicitations; USAspending.gov has the awards. I wanted to see how much of one company I could reassemble from a single UEI, and whether polling SAM.gov was enough to alert on new opportunities without paying for GovWin or HigherGov.

I worked from GSA's [Get Opportunities API](https://open.gsa.gov/api/opportunities-api/) and the [USAspending API](https://api.usaspending.gov/).

## Contractor profile

`GET /v1/contractors/{uei}` joins SAM entity, SAM exclusions, and USAspending awards for one UEI, with a source URL and timestamp on each part.

[![Looking up a contractor by UEI](docs/figures/contractors.png)](docs/figures/contractors.png)

## Opportunities and alerts

`GET /v1/opportunities` searches and normalizes SAM.gov solicitations. A background poller re-runs saved searches and fires the webhook the first time a notice shows up.

[![The opportunity search console](docs/figures/opportunities.png)](docs/figures/opportunities.png)

## Data sources

Upstream endpoints are config, since these APIs move. `/v1/sources` reports which are wired up.

[![Configured data sources and their status](docs/figures/data-sources.png)](docs/figures/data-sources.png)

## Running it

```bash
cp .env.example .env
PYTHONPATH=src python -m uvicorn govgraph.main:app --reload
```

Console at http://127.0.0.1:8000/, or call the API:

```bash
curl -s "http://127.0.0.1:8000/v1/opportunities/search?q=software&limit=10" | python -m json.tool
```

SAM.gov endpoints need a free api.data.gov key in `.env` as `GOVGRAPH_API_DATA_GOV_KEY`; USAspending needs nothing. The poller is off by default; set `GOVGRAPH_ENABLE_POLLER=true` for live webhooks.
