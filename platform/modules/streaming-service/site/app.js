// The customer page: plain JS, no third-party code (CSP: script-src 'self').
// Every API call goes to /api/* on the same CloudFront domain, so the browser shows
// what a viewer sees, including the latency of the last call.
"use strict";

const $ = (id) => document.getElementById(id);
let config = { display_name: "Streaming", env: "dev" };

async function api(path) {
  const t0 = performance.now();
  try {
    const r = await fetch(`/api${path}`, { headers: { Accept: "application/json" } });
    $("latency").textContent = `${Math.round(performance.now() - t0)} ms (${r.status})`;
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    $("status").textContent = "";
    return await r.json();
  } catch (e) {
    $("status").textContent = `API error: ${e.message}`;
    throw e;
  }
}

function card(t, onClick) {
  const li = document.createElement("li");
  const b = document.createElement("button");
  b.type = "button";
  b.className = `card genre-${t.genre || "other"}`;
  const name = document.createElement("strong");
  name.textContent = t.name || t.id;
  const info = document.createElement("span");
  info.textContent = [t.genre, t.year].filter(Boolean).join(" · ");
  b.append(name, info);
  b.addEventListener("click", () => onClick(t.id));
  li.append(b);
  return li;
}

async function showList(path, heading) {
  const data = await api(path);
  $("list-heading").textContent = heading;
  $("titles").replaceChildren(...data.titles.map((t) => card(t, openTitle)));
  $("player").hidden = true;
  $("browse").hidden = false;
}

const names = new Map();

async function openTitle(id) {
  const t = await api(`/titles/${encodeURIComponent(id)}`);
  names.set(t.id, t);
  $("title-name").textContent = t.name;
  $("title-info").textContent = [t.genre, t.year].filter(Boolean).join(" · ");
  $("player").hidden = false;
  $("browse").hidden = true;
  $("recs").replaceChildren();
  $("recs-source").textContent = "loading";
  const recs = await api(`/titles/${encodeURIComponent(id)}/recs`);
  // "vendor" = live from the recommendations vendor; "fallback" = last known list
  // from DynamoDB (the vendor_fallback runtime flag, scenario S1).
  $("recs-source").textContent = recs.source;
  $("recs-source").className = `badge source-${recs.source}`;
  $("recs").replaceChildren(...recs.recs.map((rid) => card(names.get(rid) || { id: rid }, openTitle)));
}

async function main() {
  try {
    config = await (await fetch("/config.json")).json();
  } catch (_) { /* keep defaults */ }
  document.title = config.display_name;
  $("brand").textContent = config.display_name;
  document.body.dataset.catalog = config.catalog || "tv";
  $("env").textContent = `${config.customer} · ${config.env}`;

  const all = await api("/catalog");
  all.titles.forEach((t) => names.set(t.id, t));
  await showList("/catalog", "All titles");

  $("search").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const q = $("q").value.trim();
    if (q) showList(`/search?q=${encodeURIComponent(q)}`, `Results for "${q}"`);
    else showList("/catalog", "All titles");
  });
  $("back").addEventListener("click", () => showList("/catalog", "All titles"));
}

main();
