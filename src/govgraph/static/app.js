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
    const message = json?.detail || json?.raw || `HTTP ${resp.status}`;
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
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

function prettyJson(obj) {
  return JSON.stringify(obj, null, 2);
}

function renderPage(title, subtitle, bodyChildren) {
  const view = $("#view");
  view.innerHTML = "";
  view.appendChild(el("h1", { class: "h1" }, [title]));
  view.appendChild(el("p", { class: "sub" }, [subtitle]));
  for (const child of bodyChildren) view.appendChild(child);
}

function renderError(err) {
  const hint =
    err?.status === 401
      ? "Unauthorized. If GOVGRAPH_API_KEY is set on the server, add it in the Auth panel."
      : "Check upstream connectivity and keys (.env).";
  return el("div", { class: "card" }, [
    el("div", { class: "pill bad" }, ["Error", ` • ${err.message}`]),
    el("p", { class: "muted" }, [hint]),
    el("div", { class: "code" }, [prettyJson(err.payload || { message: err.message, status: err.status })]),
  ]);
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
      hint.textContent = "This server requires an API key. Save it above to use the console.";
    } else {
      hint.textContent = "No API key required (local dev default).";
    }
    setStatus("Connected", "ok");
  } catch (e) {
    setStatus("Not connected", "bad");
  }
}

function renderSources() {
  const sources = state.config?.sources || [];
  const table = el("table", { class: "table" }, [
    el("thead", {}, [
      el("tr", {}, [el("th", {}, ["Source"]), el("th", {}, ["Base URL"]), el("th", {}, ["Configured"])]),
    ]),
    el(
      "tbody",
      {},
      sources.map((s) =>
        el("tr", {}, [
          el("td", {}, [s.name]),
          el("td", {}, [el("span", { class: "code" }, [s.base_url])]),
          el("td", {}, [s.configured ? "yes" : "no"]),
        ])
      )
    ),
  ]);
  renderPage("Sources", "Configured upstream base URLs and feature flags.", [table]);
}

function renderOpportunities() {
  const q = el("input", { class: "input", placeholder: "search keywords (e.g., cybersecurity)", value: "software" });
  const postedFrom = el("input", { class: "input", type: "date" });
  const postedTo = el("input", { class: "input", type: "date" });
  const limit = el("input", { class: "input", type: "number", value: "25", min: "1", max: "100" });
  const offset = el("input", { class: "input", type: "number", value: "0", min: "0" });
  const results = el("div", { class: "grid" }, []);
  const chartWrap = el("div", { class: "card" }, [el("div", { class: "card-title" }, ["Opportunities over time"])]);
  const chart = el("div", { id: "opp-chart" }, []);
  chartWrap.appendChild(chart);

  async function runSearch() {
    results.innerHTML = "";
    results.appendChild(el("div", { class: "pill" }, ["Loading…"]));
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
      results.appendChild(el("div", { class: "pill ok" }, [`${items.length} items`]));

      const table = el("table", { class: "table" }, [
        el("thead", {}, [
          el("tr", {}, [el("th", {}, ["ID"]), el("th", {}, ["Title"]), el("th", {}, ["Posted"])]),
        ]),
        el(
          "tbody",
          {},
          items.map((it) =>
            el("tr", {}, [
              el("td", {}, [el("span", { class: "code" }, [it.external_id])]),
              el("td", {}, [it.title || "—"]),
              el("td", {}, [fmtDate(it.posted_at)]),
            ])
          )
        ),
      ]);
      results.appendChild(table);

      renderOpportunitiesChart(items);
    } catch (err) {
      results.innerHTML = "";
      results.appendChild(renderError(err));
    }
  }

  const form = el("div", { class: "grid" }, [
    el("div", { class: "grid grid-2" }, [
      el("div", {}, [el("div", { class: "label" }, ["Query"]), q]),
      el("div", {}, [
        el("div", { class: "label" }, ["Limit / Offset"]),
        el("div", { class: "row" }, [limit, offset]),
      ]),
    ]),
    el("div", { class: "grid grid-2" }, [
      el("div", {}, [el("div", { class: "label" }, ["Posted from"]), postedFrom]),
      el("div", {}, [el("div", { class: "label" }, ["Posted to"]), postedTo]),
    ]),
    el("div", { class: "row" }, [
      el("button", { class: "btn", type: "button", onclick: runSearch }, ["Search"]),
      el("button", { class: "btn btn-secondary", type: "button", onclick: () => (results.innerHTML = "") }, ["Clear"]),
    ]),
    chartWrap,
    results,
  ]);

  renderPage("Opportunities", "Search SAM.gov opportunities and visualize posting volume.", [form]);
  runSearch();
}

function renderOpportunitiesChart(items) {
  const chartEl = $("#opp-chart");
  chartEl.innerHTML = "";
  if (typeof window.d3 === "undefined") {
    chartEl.appendChild(el("p", { class: "muted" }, ["D3 failed to load (check internet access)."]));
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
    chartEl.appendChild(el("p", { class: "muted" }, ["No dated results to chart."]));
    return;
  }

  const width = chartEl.clientWidth || 760;
  const height = 220;
  const margin = { top: 14, right: 14, bottom: 30, left: 40 };
  const svg = d3
    .select(chartEl)
    .append("svg")
    .attr("width", "100%")
    .attr("viewBox", `0 0 ${width} ${height}`);

  const x = d3
    .scaleBand()
    .domain(data.map((d) => d.day))
    .range([margin.left, width - margin.right])
    .padding(0.25);

  const y = d3
    .scaleLinear()
    .domain([0, d3.max(data, (d) => d.count)])
    .nice()
    .range([height - margin.bottom, margin.top]);

  svg
    .append("g")
    .attr("fill", "#2563eb")
    .selectAll("rect")
    .data(data)
    .join("rect")
    .attr("x", (d) => x(d.day))
    .attr("y", (d) => y(d.count))
    .attr("height", (d) => y(0) - y(d.count))
    .attr("width", x.bandwidth())
    .attr("rx", 6);

  svg
    .append("g")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(
      d3
        .axisBottom(x)
        .tickValues(data.length > 8 ? data.filter((_, i) => i % Math.ceil(data.length / 8) === 0).map((d) => d.day) : null)
    )
    .call((g) => g.selectAll("text").attr("font-size", 10).attr("fill", "#475569"))
    .call((g) => g.selectAll("path,line").attr("stroke", "#e2e8f0"));

  svg
    .append("g")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y).ticks(4))
    .call((g) => g.selectAll("text").attr("font-size", 10).attr("fill", "#475569"))
    .call((g) => g.selectAll("path,line").attr("stroke", "#e2e8f0"));
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
    chartEl.appendChild(el("p", { class: "muted" }, ["D3 failed to load (check internet access)."]));
    return;
  }
  if (!awards.length) {
    chartEl.appendChild(el("p", { class: "muted" }, ["No award rows to chart (upstream may be unavailable or schema changed)."]));
    return;
  }
  const d3 = window.d3;

  // Aggregate by agency
  const sums = new Map();
  for (const a of awards) sums.set(a.agency, (sums.get(a.agency) || 0) + a.amount);
  const data = Array.from(sums.entries())
    .map(([agency, amount]) => ({ agency, amount }))
    .sort((a, b) => b.amount - a.amount)
    .slice(0, 10);

  const width = chartEl.clientWidth || 760;
  const height = 260;
  const margin = { top: 14, right: 14, bottom: 30, left: 170 };

  const svg = d3
    .select(chartEl)
    .append("svg")
    .attr("width", "100%")
    .attr("viewBox", `0 0 ${width} ${height}`);

  const y = d3
    .scaleBand()
    .domain(data.map((d) => d.agency))
    .range([margin.top, height - margin.bottom])
    .padding(0.25);

  const x = d3
    .scaleLinear()
    .domain([0, d3.max(data, (d) => d.amount)])
    .nice()
    .range([margin.left, width - margin.right]);

  svg
    .append("g")
    .attr("fill", "#06b6d4")
    .selectAll("rect")
    .data(data)
    .join("rect")
    .attr("x", x(0))
    .attr("y", (d) => y(d.agency))
    .attr("width", (d) => x(d.amount) - x(0))
    .attr("height", y.bandwidth())
    .attr("rx", 6);

  svg
    .append("g")
    .attr("transform", `translate(0,${height - margin.bottom})`)
    .call(d3.axisBottom(x).ticks(5).tickFormat((v) => `$${d3.format(".2s")(v)}`))
    .call((g) => g.selectAll("text").attr("font-size", 10).attr("fill", "#475569"))
    .call((g) => g.selectAll("path,line").attr("stroke", "#e2e8f0"));

  svg
    .append("g")
    .attr("transform", `translate(${margin.left},0)`)
    .call(d3.axisLeft(y).tickSizeOuter(0))
    .call((g) => g.selectAll("text").attr("font-size", 10).attr("fill", "#334155"))
    .call((g) => g.selectAll("path,line").attr("stroke", "#e2e8f0"));
}

function renderContractors() {
  const uei = el("input", { class: "input", placeholder: "UEI (e.g., ABCDEFGHIJKLM)", value: "" });
  const out = el("div", { class: "grid" }, []);
  const chartCard = el("div", { class: "card" }, [
    el("div", { class: "card-title" }, ["Awards by awarding agency (top 10)"]),
    el("div", { id: "awards-chart" }, []),
  ]);

  async function runLookup() {
    out.innerHTML = "";
    out.appendChild(el("div", { class: "pill" }, ["Loading…"]));
    try {
      const data = await apiFetch(`/v1/contractors/${encodeURIComponent(uei.value.trim())}`);
      out.innerHTML = "";
      out.appendChild(el("div", { class: "pill ok" }, ["Profile loaded"]));
      out.appendChild(chartCard);
      renderAwardsChart(extractAwards(data.usaspending_awards));

      out.appendChild(el("div", { class: "hr" }, []));
      out.appendChild(el("div", { class: "card-title" }, ["Raw JSON (provenance preserved)"]));
      out.appendChild(el("div", { class: "code" }, [prettyJson(data)]));
    } catch (err) {
      out.innerHTML = "";
      out.appendChild(renderError(err));
    }
  }

  const form = el("div", { class: "grid" }, [
    el("div", {}, [el("div", { class: "label" }, ["UEI"]), uei]),
    el("div", { class: "row" }, [
      el("button", { class: "btn", type: "button", onclick: runLookup }, ["Lookup contractor"]),
      el("button", { class: "btn btn-secondary", type: "button", onclick: () => (out.innerHTML = "") }, ["Clear"]),
    ]),
    out,
  ]);

  renderPage("Contractors", "Join SAM.gov entity/exclusions + USAspending awards by UEI.", [form]);
}

function renderWebhooks() {
  const url = el("input", { class: "input", placeholder: "https://example.com/webhook" });
  const eventType = el("input", { class: "input", value: "sam.opportunity.created" });
  const filters = el("textarea", { class: "textarea" }, []);
  filters.value = JSON.stringify({ q: "software" }, null, 2);

  const out = el("div", { class: "grid" }, []);

  async function create() {
    out.innerHTML = "";
    out.appendChild(el("div", { class: "pill" }, ["Creating…"]));
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
      out.appendChild(el("div", { class: "pill ok" }, [`Created ${sub.id}`]));

      const testBtn = el(
        "button",
        {
          class: "btn",
          type: "button",
          onclick: async () => {
            const res = await apiFetch(`/v1/webhooks/subscriptions/${sub.id}/test`, { method: "POST" });
            out.appendChild(el("div", { class: "pill" }, [`Test delivery: ${res.delivery_id}`]));
          },
        },
        ["Send test"]
      );

      out.appendChild(el("div", { class: "row" }, [testBtn]));
      out.appendChild(el("div", { class: "card-title" }, ["Subscription JSON"]));
      out.appendChild(el("div", { class: "code" }, [prettyJson(sub)]));
    } catch (err) {
      out.innerHTML = "";
      out.appendChild(renderError(err));
    }
  }

  const form = el("div", { class: "grid" }, [
    el("div", {}, [el("div", { class: "label" }, ["Webhook URL"]), url]),
    el("div", {}, [el("div", { class: "label" }, ["Event type"]), eventType]),
    el("div", {}, [el("div", { class: "label" }, ["Filters (JSON)"]), filters]),
    el("div", { class: "row" }, [el("button", { class: "btn", type: "button", onclick: create }, ["Create subscription"])]),
    out,
  ]);

  renderPage(
    "Webhooks",
    "Create a subscription and receive events. Enable the background poller in .env to emit real SAM opportunity events.",
    [form]
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

