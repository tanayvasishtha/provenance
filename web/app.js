const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
let SHORT = new Set();
const fmt = new Intl.NumberFormat("en-US");

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function el(tag, attrs = {}, text) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}

function title(name) {
  return name.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

function setState(node, text, isError = false) {
  node.textContent = text;
  node.classList.toggle("error", isError);
  node.hidden = !text;
}

/* Views: real anchors, one visible at a time, back button works. */
function showView() {
  const view = (location.hash || "#check").slice(1);
  document.querySelectorAll("[data-view-panel]").forEach((p) => (p.hidden = p.dataset.viewPanel !== view));
  document.querySelectorAll(".tabs a").forEach((a) =>
    a.dataset.view === view ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
  if (view === "risk") loadRisk();
}
window.addEventListener("hashchange", showView);

async function loadMeta() {
  try {
    const m = await api("/api/meta");
    $("#freshness").innerHTML = "";
    $("#freshness").append(
      `Graph built from FDA public records: ${fmt.format(m.plants)} plants, ${fmt.format(m.products)} products, `
      + `${fmt.format(m.current_shortages)} current shortage entries. Mode: `,
      el("span", { class: "code" }, m.mode), ".");
  } catch (e) {
    setState($("#freshness"), `Graph not reachable (${e.message}). Start FalkorDB with scripts/falkordb-wsl.sh, then reload.`, true);
  }
}

async function loadDrugs() {
  const sel = $("#drug");
  try {
    const { short, other } = await api("/api/drugs");
    SHORT = new Set(short);
    sel.innerHTML = "";
    sel.append(el("option", { value: "" }, "Choose a drug"));
    const group = (label, list) => {
      const g = el("optgroup", { label });
      list.forEach((d) => g.append(el("option", { value: d }, title(d))));
      sel.append(g);
    };
    group(`On the FDA shortage list now (${short.length})`, short);
    group("Other drugs in the graph", other);
    const wanted = (new URLSearchParams(location.search).get("drug") || "").toUpperCase();
    if (wanted && [...short, ...other].includes(wanted)) { sel.value = wanted; check(wanted); }
  } catch (e) {
    sel.innerHTML = "";
    sel.append(el("option", { value: "" }, "Shortage list unavailable"));
    setState($("#check-state"), `Could not load the shortage list: ${e.message}`, true);
  }
}

/* ---------- Check a substitute ---------- */

$("#check-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const drug = $("#drug").value;
  if (drug) check(drug);
});

async function check(drug) {
  const state = $("#check-state");
  const btn = $("#check-form button");
  btn.disabled = true;
  $("#check-result").hidden = true;
  setState(state, `Tracing plants for ${title(drug)}...`);
  try {
    const [trap, rag, safe] = await Promise.all([
      api(`/api/trap?drug=${encodeURIComponent(drug)}`),
      api(`/api/rag?drug=${encodeURIComponent(drug)}`),
      api(`/api/safe?drug=${encodeURIComponent(drug)}`),
    ]);
    if (!trap.plants.length) {
      setState(state, `No label for ${title(drug)} names a manufacturing plant, so the graph has nothing to trace.`);
      return;
    }
    setState(state, "");
    // Unhide first: the chain layout depends on the container's real width.
    $("#check-result").hidden = false;
    renderCompare(drug, trap, rag, safe);
    renderChain(drug, trap, rag.rows[0]?.substitute);
    renderEvidence(trap);
    $("#check-cypher").textContent = `// Plants that make ${drug}\n${trap.plants_cypher}\n\n// Substitutes and the plants they share\n${trap.cypher}`;
    history.replaceState(null, "", `?drug=${encodeURIComponent(drug)}#check`);
  } catch (e) {
    setState(state, `The check failed: ${e.message}. Is FalkorDB running?`, true);
  } finally {
    btn.disabled = false;
  }
}

function renderCompare(drug, trap, rag, safe) {
  const list = $("#rag-list");
  list.innerHTML = "";
  if (!rag.rows.length) list.append(el("li", {}, "No label text to compare for this drug."));
  rag.rows.forEach((r) => {
    const li = el("li");
    li.append(el("span", { class: "name" }, title(r.substitute)), el("span", { class: "score" }, `distance ${r.score.toFixed(3)}`));
    list.append(li);
  });

  const v = $("#verdict");
  v.innerHTML = "";
  const pick = rag.rows[0]?.substitute;
  const row = trap.rows.find((r) => r.substitute === pick);
  const shared = row ? row.shared_plants : [];
  if (!pick) {
    v.className = "";
    v.append(el("p", { class: "head" }, "No similar-text pick to check."));
  } else if (shared.length) {
    v.className = "trap";
    v.append(el("p", { class: "head" }, `Same plant: ${title(pick)} and ${title(drug)} share ${shared.length} manufacturing plant${shared.length > 1 ? "s" : ""}.`));
    shared.forEach((p) => {
      const flag = p.flags.length ? ` Under ${p.flags.join(", ")}.` : "";
      v.append(el("p", {}, `${p.plant} (${p.country}, FEI ${p.fei}).${flag}`));
    });
    v.append(el("p", {}, `If ${shared.length > 1 ? "one of these plants" : "that plant"} is why the drug is short, the substitute is exposed too.`));
  } else if (row) {
    v.className = "";
    v.append(el("p", { class: "head" }, `${title(pick)}: no shared manufacturing plant found.`),
      el("p", {}, "Its labels name different plants from the ones making the drug in shortage."));
  } else {
    v.className = "";
    v.append(el("p", { class: "head" }, `${title(pick)} is outside this drug's FDA pharmacologic class.`),
      el("p", {}, "The graph only checks same-class substitutes, so this pick is not traced."));
  }

  const ul = $("#safe-list");
  ul.innerHTML = "";
  if (!safe.rows.length) {
    ul.append(el("li", { class: "none" }, "None. Every same-class substitute in the graph shares a plant with this drug."));
  }
  safe.rows.slice(0, 6).forEach((r) => {
    const li = el("li");
    const facts = [`${r.labels} US label${r.labels === 1 ? "" : "s"}`];
    if (r.short_now) facts.push(`${r.short_now} on the shortage list`);
    if (r.flagged_plants) facts.push(`${r.flagged_plants} plant under import alert`);
    li.append(el("span", { class: "name" }, title(r.substitute)), el("span", { class: "facts" }, facts.join(" · ")));
    ul.append(li);
  });
}

function renderEvidence(trap) {
  const tbody = $("#evidence tbody");
  tbody.innerHTML = "";
  trap.plants.forEach((p) => {
    const also = trap.rows.filter((r) => r.shared_plants.some((s) => s.fei === p.fei)).map((r) => title(r.substitute));
    const tr = el("tr");
    tr.append(el("td", {}, p.plant), el("td", { class: "num" }, p.fei || "not registered"), el("td", {}, p.country),
      el("td", { class: "num" }, String(p.products)), el("td", {}, also.join(", ") || "none in this class"));
    const alert = el("td", p.import_alert_dates.length ? { class: "alert" } : {}, p.import_alert_dates.length ? `Listed ${p.import_alert_dates.join(", ")}` : "not listed");
    tr.append(alert);
    tbody.append(tr);
  });
}

/* ---------- The chain ---------- */

function renderChain(drug, trap, ragPick) {
  const svg = $("#chain");
  svg.innerHTML = "";
  const narrow = svg.parentElement.clientWidth < 620;
  const subs = trap.rows.map((r) => r.substitute);
  const sharedFei = new Set(trap.rows.flatMap((r) => r.shared_plants.map((p) => p.fei)));
  const plants = [...trap.plants].sort((a, b) => sharedFei.has(b.fei) - sharedFei.has(a.fei)).slice(0, 8);
  const hidden = trap.plants.length - plants.length;

  const W = narrow ? 360 : 1000, nodeW = narrow ? 300 : 270, nodeH = 52, gap = 14;
  const cols = narrow ? null : [10, 365, 720];
  const pos = new Map();
  let H;
  if (narrow) {
    let y = 10;
    const place = (key) => { pos.set(key, { x: 30, y }); y += nodeH + gap; };
    place("origin"); y += 16;
    plants.forEach((p) => place(`p:${p.fei}`)); y += 16;
    subs.forEach((s) => place(`s:${s}`));
    H = y;
  } else {
    const colH = (n) => n * nodeH + (n - 1) * gap;
    H = Math.max(colH(plants.length + (hidden ? 1 : 0)), colH(subs.length), nodeH) + 20;
    const top = (n) => (H - colH(n)) / 2;
    pos.set("origin", { x: cols[0], y: top(1) });
    plants.forEach((p, i) => pos.set(`p:${p.fei}`, { x: cols[1], y: top(plants.length + (hidden ? 1 : 0)) + i * (nodeH + gap) }));
    subs.forEach((s, i) => pos.set(`s:${s}`, { x: cols[2], y: top(subs.length) + i * (nodeH + gap) }));
  }
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.style.maxWidth = narrow ? "420px" : "none";

  const ragRow = trap.rows.find((r) => r.substitute === ragPick);
  const hotFei = new Set(ragRow ? ragRow.shared_plants.map((p) => p.fei) : []);

  const edge = (a, b, hot, delay) => {
    const p1 = pos.get(a), p2 = pos.get(b);
    const path = document.createElementNS(NS, "path");
    const d = narrow
      ? `M${p1.x + 10} ${p1.y + nodeH} L${p1.x + 10} ${p2.y + nodeH / 2} L${p2.x} ${p2.y + nodeH / 2}`
      : `M${p1.x + nodeW} ${p1.y + nodeH / 2} C${p1.x + nodeW + 45} ${p1.y + nodeH / 2}, ${p2.x - 45} ${p2.y + nodeH / 2}, ${p2.x} ${p2.y + nodeH / 2}`;
    path.setAttribute("d", d);
    path.setAttribute("class", `edge draw${hot ? " hot" : ""}`);
    svg.append(path);
    const len = path.getTotalLength();
    path.style.setProperty("--len", len);
    path.style.animationDelay = `${delay}ms`;
  };
  const node = (key, label, sub, cls, delay) => {
    const p = pos.get(key);
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", `node appear ${cls}`);
    g.style.animationDelay = `${delay}ms`;
    const r = document.createElementNS(NS, "rect");
    Object.entries({ x: p.x, y: p.y, width: nodeW, height: nodeH }).forEach(([k, v]) => r.setAttribute(k, v));
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", p.x + 12); t.setAttribute("y", p.y + 22);
    const max = narrow ? 22 : 26;
    t.textContent = label.length > max ? `${label.slice(0, max - 1)}…` : label;
    const s = document.createElementNS(NS, "text");
    s.setAttribute("x", p.x + 12); s.setAttribute("y", p.y + 41); s.setAttribute("class", "sub");
    s.textContent = sub;
    g.append(r, t, s);
    svg.append(g);
  };

  // Traversal order: drug, edges to plants, plants, edges to substitutes, substitutes.
  plants.forEach((p) => edge("origin", `p:${p.fei}`, hotFei.has(p.fei), 80));
  trap.rows.forEach((r) => r.shared_plants.forEach((sp) => {
    if (pos.has(`p:${sp.fei}`)) edge(`p:${sp.fei}`, `s:${r.substitute}`, r.substitute === ragPick && hotFei.has(sp.fei), 330);
  }));
  node("origin", title(drug), SHORT.has(drug) ? "on the FDA shortage list" : "not short today", "origin", 0);
  plants.forEach((p) => {
    const flagged = p.import_alert_dates.length;
    node(`p:${p.fei}`, p.plant, flagged ? `Import alert ${p.import_alert_dates[0]}` : `FEI ${p.fei || "n/a"} · ${p.country}`,
      flagged ? "flagged" : (hotFei.has(p.fei) ? "shared" : ""), 260);
  });
  subs.forEach((s) => {
    const shared = trap.rows.find((r) => r.substitute === s).shared_plants.length;
    node(`s:${s}`, title(s), shared ? `shares ${shared} plant${shared > 1 ? "s" : ""}` : "no shared plant",
      s === ragPick && shared ? "shared" : "", 520);
  });
  if (hidden && !narrow) {
    const last = pos.get(`p:${plants[plants.length - 1].fei}`);
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", cols[1] + 10); t.setAttribute("y", last.y + nodeH + gap + 22);
    t.setAttribute("class", "appear"); t.style.font = "400 12px var(--sans)"; t.style.fill = "var(--ink-2)";
    t.textContent = `and ${hidden} more plant${hidden > 1 ? "s" : ""}, listed in the evidence table`;
    svg.append(t);
  }

  const sharedSubs = trap.rows.filter((r) => r.shared_plants.length).map((r) => title(r.substitute));
  $("#chain-desc").textContent = `${title(drug)} is made in ${trap.plants.length} plant${trap.plants.length > 1 ? "s" : ""}. `
    + (sharedSubs.length ? `${sharedSubs.join(", ")} ${sharedSubs.length > 1 ? "come" : "comes"} out of at least one of them.` : "No same-class substitute shares a plant with it.");
}

/* ---------- If a plant stops ---------- */

let plantsLoaded = false;
async function loadPlants() {
  if (plantsLoaded) return;
  const sel = $("#plant");
  try {
    const { flagged, exposed } = await api("/api/plants");
    sel.innerHTML = "";
    sel.append(el("option", { value: "" }, "Choose a plant"));
    const group = (label, list) => {
      if (!list.length) return;
      const g = el("optgroup", { label });
      list.forEach((r) => g.append(el("option", { value: r.fei }, `${r.plant} (${r.country}, FEI ${r.fei})`)));
      sel.append(g);
    };
    group("Under FDA import alert 66-40", flagged);
    group("Most exposure to today's shortage drugs", exposed);
    plantsLoaded = true;
  } catch (e) {
    setState($("#plant-state"), `Could not load plants: ${e.message}`, true);
  }
}

$("#plant-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fei = $("#plant").value;
  if (!fei) return;
  const state = $("#plant-state");
  setState(state, "Finding every drug this plant makes...");
  $("#plant-result").hidden = true;
  try {
    const res = await api(`/api/plant/${encodeURIComponent(fei)}`);
    const tbody = $("#plant-table tbody");
    tbody.innerHTML = "";
    res.rows.forEach((r) => {
      const tr = el("tr", r.short_now ? { class: "short" } : {});
      tr.append(el("td", {}, title(r.ingredient)), el("td", { class: "num" }, `${r.share_pct}%`),
        el("td", { class: "num" }, String(r.from_plant)), el("td", { class: "num" }, String(r.all_products)),
        el("td", r.short_now ? { class: "alert" } : {}, r.short_now ? `Yes, ${r.short_now} entr${r.short_now > 1 ? "ies" : "y"}` : "No"));
      tbody.append(tr);
    });
    $("#plant-cypher").textContent = res.cypher;
    setState(state, `${res.rows[0].plant}: ${res.rows.length} drug${res.rows.length > 1 ? "s" : ""} depend on it.`);
    $("#plant-result").hidden = false;
    renderPlantAlerts(fei);
  } catch (e) {
    setState(state, `Could not trace this plant: ${e.message}`, true);
  }
});

/* ---------- Where the risk sits ---------- */

let riskLoaded = false;
async function loadRisk() {
  if (riskLoaded) return;
  const state = $("#risk-state");
  try {
    const res = await api("/api/chokepoints?limit=25");
    const tbody = $("#risk-table tbody");
    tbody.innerHTML = "";
    res.rows.forEach((r) => {
      const tr = el("tr");
      const deps = r.top.filter((d) => d.share >= 1).sort((a, b) => b.share - a.share).slice(0, 6);
      const drugs = deps.map((d) => `${title(d.ingredient)} ${d.share}%`).join(", ");
      tr.append(el("td", { class: "num" }, r.exposure.toFixed(2)), el("td", {}, `${r.plant} `),
        el("td", {}, r.country), el("td", {}, drugs + (r.ingredients > deps.length ? `, and ${r.ingredients - deps.length} more` : "")),
        el("td", r.import_alert_dates.length ? { class: "alert" } : {}, r.import_alert_dates.length ? `Listed ${r.import_alert_dates.join(", ")}` : "not listed"));
      tr.children[1].append(el("span", { class: "code" }, `FEI ${r.fei}`));
      tbody.append(tr);
    });
    $("#risk-cypher").textContent = res.cypher;
    setState(state, "");
    $("#risk-table").hidden = false;
    riskLoaded = true;
  } catch (e) {
    setState(state, `Could not rank plants: ${e.message}`, true);
  }
}

loadMeta();
loadDrugs();
loadPlants();
showView();
