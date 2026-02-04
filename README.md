# GovGraph

One HTTP service over the federal procurement APIs: it returns clean JSON from SAM.gov and USAspending.gov, and POSTs a signed webhook when a new opportunity matches a search you saved.

A contractor's record with the government is split across systems that don't share keys. SAM.gov holds the registration, the exclusions list, and the open solicitations. USAspending.gov holds what has actually been awarded and to whom. FPDS holds the older contract history and is still being folded into SAM. I wanted to know how much of one company's footprint I could reassemble from a single UEI, and whether polling SAM.gov on a schedule was enough to get a useful new-opportunity alert without paying for GovWin or HigherGov. GovGraph is the service I wrote to find out.

I worked from GSA's [Get Opportunities API](https://open.gsa.gov/api/opportunities-api/) and the [USAspending API](https://api.usaspending.gov/). Both are free. Neither hands back a contractor's record joined across them.

## Contractor profile

`GET /v1/contractors/{uei}` fans out to SAM entity, SAM exclusions, and USAspending awards for one UEI, then returns them in a single response with a source URL and timestamp on each part, so you can see where every field came from.

[![Looking up a contractor by UEI](docs/figures/contractors.png)](docs/figures/contractors.png)

SAM lookups need an api.data.gov key. USAspending does not, so the profile fills in as far as the configured keys allow.

## Opportunities and alerts

`GET /v1/opportunities` searches SAM.gov solicitations and normalizes the results. A background poller re-runs saved searches on an interval and fires the webhook the first time a notice shows up, so a new RFP can land in Slack or your own endpoint without anyone watching the portal.

[![The opportunity search console](docs/figures/opportunities.png)](docs/figures/opportunities.png)

With no key configured the console says so instead of returning an empty result.

## Data sources

Every upstream endpoint is set in config, because these APIs move and change paths. `/v1/sources` reports which ones are wired up and which still need a key.

[![Configured data sources and their status](docs/figures/data-sources.png)](docs/figures/data-sources.png)

USAspending is reachable out of the box. The three SAM endpoints turn green once an api.data.gov key is set.

## Running it

```bash
cp .env.example .env
PYTHONPATH=src python -m uvicorn govgraph.main:app --reload
```

Open http://127.0.0.1:8000/ for the console, or call the API directly:

```bash
curl -s "http://127.0.0.1:8000/v1/opportunities/search?q=software&limit=10" | python -m json.tool
```

Most SAM.gov endpoints need a free api.data.gov key in `.env` as `GOVGRAPH_API_DATA_GOV_KEY`. USAspending needs nothing. The poller is off by default; set `GOVGRAPH_ENABLE_POLLER=true` when you want live webhook deliveries.

Made by Roy Vaid
