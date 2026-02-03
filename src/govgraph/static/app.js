// GovGraph Console - Professional Business Dashboard
const $ = (sel) => document.querySelector(sel);

const state = {
  apiKey: localStorage.getItem("govgraph_api_key") || "",
  config: null,
  route: "opportunities",
};

function setStatus(text, kind = "neutral") {
  const el = $("#status");
  el.textContent = text;
  el.classList.remove("ok", "bad");
  if (kind === "ok") el.classList.add("ok");
  if (kind === "bad") el.classList.add("bad");
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.apiKey) headers.set("X-Api-Key", state.apiKey);
  headers.set("Accept", "application/json");
  const resp = await fetch(path, { ...options, headers });
  const text = await resp.text();
  let json;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = { raw: text };
  }
  if (!resp.ok) {
    const detail = json?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : typeof detail === "object" && detail
          ? detail.message || detail.upstream_body_text || JSON.stringify(detail)
          : json?.raw || `HTTP ${resp.status}`;
    const err = new Error(message);
    err.status = resp.status;
    err.payload = json;
    throw err;
  }
  return json;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, String(v));
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    if (typeof child === "string") node.appendChild(document.createTextNode(child));
    else node.appendChild(child);
  }
  return node;
}

function fmtDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function prettyJson(obj) {
  return JSON.stringify(obj, null, 2);
}

function renderPage(title, subtitle, bodyChildren) {
  const view = $("#view");
  view.innerHTML = "";
  view.appendChild(el("h1", { class: "page-title" }, [title]));
  view.appendChild(el("p", { class: "page-subtitle" }, [subtitle]));
  for (const child of bodyChildren) view.appendChild(child);
}

function renderError(err) {
  const detail = err?.payload?.detail;
  const isUpstream = typeof detail === "object" && detail?.error === "upstream_error";
  const hint = isUpstream
    ? detail.message
    : err?.status === 401
      ? "Unauthorized. If GOVGRAPH_API_KEY is set on the server, add it in the sidebar."
      : "Check upstream connectivity and keys (.env).";

  const box = el("div", { class: "status-box error" }, [
    el("div", { class: "status-title" }, ["Error"]),
    el("div", { class: "status-text" }, [hint]),
  ]);

  const details = el("div", { class: "code-block" }, [
    prettyJson(err.payload || { message: err.message, status: err.status }),
  ]);

  return el("div", {}, [box, details]);
}

function attachNav() {
  for (const btn of document.querySelectorAll("[data-route]")) {
    btn.addEventListener("click", () => {
      state.route = btn.getAttribute("data-route");
      for (const b of document.querySelectorAll("[data-route]")) b.classList.remove("is-active");
      btn.classList.add("is-active");
      route();
    });
  }
}

function setupAuthPanel() {
  const input = $("#api-key");
  input.value = state.apiKey;
  $("#save-key").addEventListener("click", () => {
    state.apiKey = input.value.trim();
    localStorage.setItem("govgraph_api_key", state.apiKey);
    route();
  });
}

async function loadConfig() {
  try {
    state.config = await apiFetch("/public/config");
    const hint = $("#auth-hint");
    if (state.config.requires_api_key) {
      hint.textContent = "This server requires an API key.";
    } else {
      hint.textContent = "No API key required.";
    }
    setStatus("Connected", "ok");
  } catch (e) {
    setStatus("Not connected", "bad");
  }
}

function renderSources() {
  const sources = state.config?.sources || [];
  const table = el("table", { class: "data-table" }, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", {}, ["Source"]),
        el("th", {}, ["Base URL"]),
        el("th", {}, ["Status"]),
      ]),
    ]),
    el(
      "tbody",
      {},
      sources.map((s) =>
        el("tr", {}, [
          el("td", {}, [s.name]),
          el("td", { class: "mono" }, [s.base_url]),
          el("td", {}, [
            el("span", { class: s.configured ? "badge badge-success" : "badge badge-error" }, [
              s.configured ? "Configured" : "Not configured",
            ]),
          ]),
        ])
      )
    ),
  ]);
  renderPage("Data Sources", "Configured upstream API endpoints and their status.", [table]);
}

function renderOpportunities() {
  const sam = (state.config?.sources || []).find((s) => s.name === "sam.opportunities");
  const samConfigured = !!sam?.configured;

  const q = el("input", { class: "field-input", placeholder: "Enter search keywords", value: "software" });
  const postedFrom = el("input", { class: "field-input", type: "date" });
  const postedTo = el("input", { class: "field-input", type: "date" });
  const limit = el("input", { class: "field-input", type: "number", value: "25", min: "1", max: "100", style: "width: 80px" });
  const offset = el("input", { class: "field-input", type: "number", value: "0", min: "0", style: "width: 80px" });
  const results = el("div", {}, []);
  const chartContainer = el("div", { class: "chart-container" }, [
    el("div", { class: "chart-title" }, ["Opportunities by Posted Date"]),
    el("div", { id: "opp-chart" }, []),
  ]);

  async function runSearch() {
    results.innerHTML = "";
    results.appendChild(el("div", { class: "status-box info" }, [el("span", {}, ["Loading results..."])]));
    try {
      const params = new URLSearchParams();
      if (q.value.trim()) params.set("q", q.value.trim());
      if (postedFrom.value) params.set("posted_from", postedFrom.value);
      if (postedTo.value) params.set("posted_to", postedTo.value);
      params.set("limit", limit.value || "25");
      params.set("offset", offset.value || "0");

      const data = await apiFetch(`/v1/opportunities?${params.toString()}`);
      const items = data.items || [];
      results.innerHTML = "";

      results.appendChild(
        el("div", { class: "results-count" }, [
          "Showing ",
          el("strong", {}, [String(items.length)]),
          " results",
        ])
      );

      const table = el("table", { class: "data-table" }, [
        el("thead", {}, [
          el("tr", {}, [el("th", {}, ["ID"]), el("th", {}, ["Title"]), el("th", {}, ["Posted Date"])]),
        ]),
        el(
          "tbody",
          {},
          items.map((it) =>
            el("tr", {}, [
              el("td", { class: "mono" }, [it.external_id]),
              el("td", {}, [it.title || "-"]),
              el("td", {}, [fmtDate(it.posted_at)]),
            ])
          )
        ),
      ]);
      results.appendChild(table);
      results.appendChild(chartContainer);

      renderOpportunitiesChart(items);
    } catch (err) {
      results.innerHTML = "";
      results.appendChild(renderError(err));
    }
  }

  const warningBox = !samConfigured
    ? el("div", { class: "status-box warning" }, [
        el("div", { class: "status-title" }, ["Configuration Required"]),
        el("div", { class: "status-text" }, [
          "Opportunity search requires an api.data.gov key. Set GOVGRAPH_API_DATA_GOV_KEY in .env and restart the server.",
        ]),
      ])
    : null;

  const formContent = el("div", { class: "form-grid" }, [
    warningBox,
    el("div", { class: "form-row" }, [
      el("div", { class: "form-group form-group-half" }, [
        el("label", { class: "field-label" }, ["Search Query"]),
        q,
      ]),
      el("div", { class: "form-group" }, [
        el("label", { class: "field-label" }, ["Limit"]),
        limit,
      ]),
      el("div", { class: "form-group" }, [
        el("label", { class: "field-label" }, ["Offset"]),
        offset,
      ]),
    ]),
    el("div", { class: "form-row" }, [
      el("div", { class: "form-group form-group-half" }, [
        el("label", { class: "field-label" }, ["Posted From"]),
        postedFrom,
      ]),
      el("div", { class: "form-group form-group-half" }, [
        el("label", { class: "field-label" }, ["Posted To"]),
        postedTo,
      ]),
    ]),
    el("div", { class: "form-actions" }, [
      el("button", { class: "btn", type: "button", onclick: runSearch }, ["Search"]),
      el("button", { class: "btn btn-secondary", type: "button", onclick: () => (results.innerHTML = "") }, ["Clear Results"]),
    ]),
    results,
  ]);

  renderPage("Opportunities", "Search SAM.gov procurement opportunities.", [formContent]);
  if (samConfigured) runSearch();
}

function renderOpportunitiesChart(items) {
  const chartEl = $("#opp-chart");
  chartEl.innerHTML = "";
  if (typeof window.d3 === "undefined") {
    chartEl.appendChild(el("p", { class: "hint" }, ["Chart library failed to load."]));
    return;
  }
  const d3 = window.d3;

  const counts = new Map();
  for (const it of items) {
    if (!it.posted_at) continue;
    const day = it.posted_at.slice(0, 10);
    counts.set(day, (counts.get(day) || 0) + 1);
  }
  const data = Array.from(counts.entries())
    .map(([day, count]) => ({ day, count }))
    .sort((a, b) => (a.day < b.day ? -1 : 1));

  if (!data.length) {
    chartEl.appendChild(el("p", { class: "hint" }, ["No dated results to display."]));
    return;
  }

  const width = chartEl.clientWidth || 700;
  const height = 200;
  const margin = { top: 10, right: 10, bottom: 30, left: 40 };
  const svg = d3
    .select(chartEl)
    .append("svg")
    .attr("width", "100%")
    .attr("viewBox", `0 0 ${width} ${height}`);

  const x = d3
    .scaleBand()
    .domain(data.map((d) => d.day))
    .range([margin.left, width - margin.right])
    .padding(0.2);

  const y = d3
    .scaleLinear()
    .domain([0, d3.max(data, (d) => d.count)])
    .nice()
    .range([height - margin.bottom, margin.top]);

  svg
    .append("g")
    .attr("fill", "#ff9900")
    .selectAll("rect")
    .data(data)
    .join("rect")
    .attr("x", (d) => x(d.day))
    .attr("y", (d) => y(d.count))
    .attr("height", (d) => y(0) - y(d.count))
    .attr("width", x.bandwidth());

  svg
    .append("g")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(
      d3
        .axisBottom(x)
        .tickValues(data.length > 8 ? data.filter((_, i) => i % Math.ceil(data.length / 8) === 0).map((d) => d.day) : null)
    )
    .call((g) => g.selectAll("text").attr("font-size", 11).attr("fill", "#616161"))
    .call((g) => g.selectAll("path,line").attr("stroke", "#e0e0e0"));

  svg
    .append("g")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y).ticks(4))
    .call((g) => g.selectAll("text").attr("font-size", 11).attr("fill", "#616161"))
    .call((g) => g.selectAll("path,line").attr("stroke", "#e0e0e0"));
}

function extractAwards(raw) {
  const results = raw?.results || raw?.data?.results || [];
  if (!Array.isArray(results)) return [];
  const out = [];
  for (const row of results) {
    if (!row || typeof row !== "object") continue;
    const agency = row["Awarding Agency"] || row["awarding_agency"] || row["awarding_agency_name"] || row["Awarding Agency Name"];
    const amountRaw = row["Award Amount"] || row["award_amount"] || row["Award Amount (USD)"];
    const amount = typeof amountRaw === "number" ? amountRaw : Number(String(amountRaw || "").replace(/[$,]/g, ""));
    if (!agency || !Number.isFinite(amount)) continue;
    out.push({ agency: String(agency), amount });
  }
  return out;
}

function renderAwardsChart(awards) {
  const chartEl = $("#awards-chart");
  chartEl.innerHTML = "";
  if (typeof window.d3 === "undefined") {
    chartEl.appendChild(el("p", { class: "hint" }, ["Chart library failed to load."]));
    return;
  }
  if (!awards.length) {
    chartEl.appendChild(el("p", { class: "hint" }, ["No award data available to display."]));
    return;
  }
  const d3 = window.d3;

  const sums = new Map();
  for (const a of awards) sums.set(a.agency, (sums.get(a.agency) || 0) + a.amount);
  const data = Array.from(sums.entries())
    .map(([agency, amount]) => ({ agency, amount }))
    .sort((a, b) => b.amount - a.amount)
    .slice(0, 10);

  const width = chartEl.clientWidth || 700;
  const height = 240;
  const margin = { top: 10, right: 10, bottom: 30, left: 160 };

  const svg = d3
    .select(chartEl)
    .append("svg")
    .attr("width", "100%")
    .attr("viewBox", `0 0 ${width} ${height}`);

  const y = d3
    .scaleBand()
    .domain(data.map((d) => d.agency))
    .range([margin.top, height - margin.bottom])
    .padding(0.2);

  const x = d3
    .scaleLinear()
    .domain([0, d3.max(data, (d) => d.amount)])
    .nice()
    .range([margin.left, width - margin.right]);

  svg
    .append("g")
    .attr("fill", "#232f3e")
    .selectAll("rect")
    .data(data)
    .join("rect")
    .attr("x", x(0))
    .attr("y", (d) => y(d.agency))
    .attr("width", (d) => x(d.amount) - x(0))
    .attr("height", y.bandwidth());

  svg
    .append("g")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(d3.axisBottom(x).ticks(5).tickFormat((v) => `$${d3.format(".2s")(v)}`))
    .call((g) => g.selectAll("text").attr("font-size", 11).attr("fill", "#616161"))
    .call((g) => g.selectAll("path,line").attr("stroke", "#e0e0e0"));

  svg
    .append("g")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y).tickSizeOuter(0))
    .call((g) => g.selectAll("text").attr("font-size", 11).attr("fill", "#424242"))
    .call((g) => g.selectAll("path,line").attr("stroke", "#e0e0e0"));
}

function renderContractors() {
  const sam = (state.config?.sources || []).find((s) => s.name === "sam.entity");
  const samConfigured = !!sam?.configured;

  const uei = el("input", { class: "field-input", placeholder: "Enter UEI (e.g., ABCDEFGHIJKLM)", value: "" });
  const out = el("div", {}, []);
  const chartContainer = el("div", { class: "chart-container" }, [
    el("div", { class: "chart-title" }, ["Awards by Agency (Top 10)"]),
    el("div", { id: "awards-chart" }, []),
  ]);

  async function runLookup() {
    out.innerHTML = "";
    out.appendChild(el("div", { class: "status-box info" }, [el("span", {}, ["Loading profile..."])]));
    try {
      const data = await apiFetch(`/v1/contractors/${encodeURIComponent(uei.value.trim())}`);
      out.innerHTML = "";

      out.appendChild(
        el("div", { class: "status-box success" }, [
          el("span", {}, ["Profile loaded for UEI: " + data.uei]),
        ])
      );

      out.appendChild(chartContainer);
      renderAwardsChart(extractAwards(data.usaspending_awards));

      out.appendChild(el("hr", { class: "divider" }, []));
      out.appendChild(el("div", { class: "card-title" }, ["Raw JSON Response"]));
      out.appendChild(el("div", { class: "code-block" }, [prettyJson(data)]));
    } catch (err) {
      out.innerHTML = "";
      out.appendChild(renderError(err));
    }
  }

  const warningBox = !samConfigured
    ? el("div", { class: "status-box warning" }, [
        el("div", { class: "status-title" }, ["Partial Data Available"]),
        el("div", { class: "status-text" }, [
          "SAM entity and exclusions lookups require GOVGRAPH_API_DATA_GOV_KEY. USAspending data may still work.",
        ]),
      ])
    : null;

  const formContent = el("div", { class: "form-grid" }, [
    warningBox,
    el("div", { class: "form-group" }, [
      el("label", { class: "field-label" }, ["Unique Entity Identifier (UEI)"]),
      uei,
    ]),
    el("div", { class: "form-actions" }, [
      el("button", { class: "btn", type: "button", onclick: runLookup }, ["Look Up Contractor"]),
      el("button", { class: "btn btn-secondary", type: "button", onclick: () => (out.innerHTML = "") }, ["Clear"]),
    ]),
    out,
  ]);

  renderPage("Contractors", "Look up contractor profiles by joining SAM.gov and USAspending data.", [formContent]);
}

function renderWebhooks() {
  const url = el("input", { class: "field-input", placeholder: "https://your-endpoint.com/webhook" });
  const eventType = el("input", { class: "field-input", value: "sam.opportunity.created" });
  const filters = el("textarea", { class: "field-textarea" }, []);
  filters.value = JSON.stringify({ q: "software" }, null, 2);

  const out = el("div", {}, []);

  async function create() {
    out.innerHTML = "";
    out.appendChild(el("div", { class: "status-box info" }, [el("span", {}, ["Creating subscription..."])]));
    try {
      const payload = {
        url: url.value.trim(),
        event_type: eventType.value.trim(),
        filters: JSON.parse(filters.value || "{}"),
      };
      const sub = await apiFetch("/v1/webhooks/subscriptions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      out.innerHTML = "";
      out.appendChild(
        el("div", { class: "status-box success" }, [
          el("span", {}, ["Subscription created: " + sub.id]),
        ])
      );

      const testBtn = el(
        "button",
        {
          class: "btn btn-secondary",
          type: "button",
          onclick: async () => {
            const res = await apiFetch(`/v1/webhooks/subscriptions/${sub.id}/test`, { method: "POST" });
            out.appendChild(
              el("div", { class: "status-box info", style: "margin-top: 12px" }, [
                el("span", {}, ["Test delivery sent: " + res.delivery_id]),
              ])
            );
          },
        },
        ["Send Test Webhook"]
      );

      out.appendChild(el("div", { class: "form-actions", style: "margin-top: 12px" }, [testBtn]));
      out.appendChild(el("div", { class: "card-title", style: "margin-top: 16px" }, ["Subscription Details"]));
      out.appendChild(el("div", { class: "code-block" }, [prettyJson(sub)]));
    } catch (err) {
      out.innerHTML = "";
      out.appendChild(renderError(err));
    }
  }

  const formContent = el("div", { class: "form-grid" }, [
    el("div", { class: "form-group" }, [
      el("label", { class: "field-label" }, ["Webhook URL"]),
      url,
    ]),
    el("div", { class: "form-group" }, [
      el("label", { class: "field-label" }, ["Event Type"]),
      eventType,
    ]),
    el("div", { class: "form-group" }, [
      el("label", { class: "field-label" }, ["Filters (JSON)"]),
      filters,
    ]),
    el("div", { class: "form-actions" }, [
      el("button", { class: "btn", type: "button", onclick: create }, ["Create Subscription"]),
    ]),
    out,
  ]);

  renderPage(
    "Webhooks",
    "Create webhook subscriptions to receive event notifications. Enable the background poller in .env for live events.",
    [formContent]
  );
}

async function route() {
  if (!state.config) await loadConfig();
  if (state.route === "sources") return renderSources();
  if (state.route === "opportunities") return renderOpportunities();
  if (state.route === "contractors") return renderContractors();
  if (state.route === "webhooks") return renderWebhooks();
  renderSources();
}

async function main() {
  attachNav();
  setupAuthPanel();
  $("#open-docs").addEventListener("click", () => window.open("/docs", "_blank"));
  await loadConfig();
  await route();
}

main();
