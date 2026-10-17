/* Ask the agent, and each hospital's memory graph. Uses $, api, el, title, setState from app.js. */

const MODE_TEXT = {
  live: "The model chose the graph queries below and wrote the answer from their rows.",
  "graph-only": "Computed just now: a rule-based planner chose the graph queries below and wrote the answer from their rows. With a model key set, the model does the choosing.",
};

async function loadHospitals() {
  const sel = $("#hospital");
  try {
    const list = await api("/api/hospitals");
    sel.innerHTML = "";
    list.forEach((h) => sel.append(el("option", { value: h.slug }, h.name)));
    renderMemory(sel.value);
  } catch (e) {
    setState($("#ask-state"), `Could not load hospitals: ${e.message}`, true);
  }
}

async function renderMemory(slug) {
  if (!slug) return;
  const stocks = $("#mem-stocks"), dec = $("#mem-decisions");
  try {
    const p = await api(`/api/hospital/${encodeURIComponent(slug)}`);
    stocks.innerHTML = ""; dec.innerHTML = "";
    p.stocks.forEach((d) => stocks.append(el("li", {}, title(d))));
    if (!p.decisions.length) dec.append(el("li", {}, "None yet. Tell the agent which substitute you chose and it records it here."));
    p.decisions.forEach((d) => {
      const li = el("li", {}, `${title(d.drug)} switched to ${title(d.substitute)}`);
      li.append(el("span", { class: "when" }, d.at.replace("T", " ").replace("Z", " UTC")));
      dec.append(li);
    });
  } catch (e) {
    dec.innerHTML = "";
    dec.append(el("li", {}, `Could not read this hospital's graph: ${e.message}`));
  }
}

$("#hospital").addEventListener("change", (e) => renderMemory(e.target.value));

document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => {
  $("#question").value = b.textContent;
  $("#question").focus();
}));

$("#forget").addEventListener("click", async () => {
  const slug = $("#hospital").value;
  await fetch(`/api/hospital/${encodeURIComponent(slug)}/forget`, { method: "POST" });
  renderMemory(slug);
});

function traceItem(t) {
  const li = el("li");
  li.append(el("span", { class: "tool" }, t.tool), " ", el("span", { class: "args" }, JSON.stringify(t.input)),
    ` returned ${t.rows} row${t.rows === 1 ? "" : "s"}`);
  const d = el("details");
  d.append(el("summary", {}, "Cypher"), el("pre", {}, t.cypher));
  li.append(d);
  return li;
}

/* Reads the server-sent events from /api/ask/stream: graph queries land as they return, the answer as it is written. */
async function* askEvents(question, hospital) {
  const res = await fetch("/api/ask/stream", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, hospital }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, cut).trim();
      buf = buf.slice(cut + 2);
      if (line.startsWith("data: ")) yield JSON.parse(line.slice(6));
    }
  }
}

$("#ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const question = $("#question").value.trim(), hospital = $("#hospital").value;
  if (question.length < 3) return;
  const btn = $("#ask-form button[type=submit]"), state = $("#ask-state");
  const answer = $("#ask-answer"), ol = $("#ask-trace"), mode = $("#ask-mode");
  btn.disabled = true;
  answer.textContent = ""; ol.innerHTML = ""; mode.innerHTML = "";
  answer.classList.add("typing");
  $("#ask-result").hidden = false;
  setState(state, "Reading the question...");
  const t0 = performance.now();
  try {
    for await (const e of askEvents(question, hospital)) {
      if (e.type === "step") setState(state, `${e.text}...`);
      else if (e.type === "reset") { answer.textContent = ""; ol.innerHTML = ""; }
      else if (e.type === "tool") {
        ol.append(traceItem(e));
        setState(state, `${e.tool} returned ${e.rows} row${e.rows === 1 ? "" : "s"}`);
      } else if (e.type === "delta") {
        if (!answer.textContent) setState(state, `Writing the answer (${((performance.now() - t0) / 1000).toFixed(1)} s)`);
        answer.textContent += e.text;
      } else if (e.type === "done") {
        const who = e.mode === "live" ? `live, ${e.model}` : e.mode;
        mode.append(el("span", { class: "code" }, `${who}, ${(e.elapsed_ms / 1000).toFixed(2)} s`), ` ${MODE_TEXT[e.mode] || ""}`);
        if (e.note) mode.append(el("span", {}, ` ${e.note}`));
      }
    }
    if (!ol.children.length) ol.append(el("li", {}, "No graph query was needed for this answer."));
    setState(state, "");
    renderMemory(hospital);
  } catch (e) {
    setState(state, `The agent could not answer: ${e.message}`, true);
  } finally {
    answer.classList.remove("typing");
    btn.disabled = false;
  }
});

/* Plant view: the same FDA event, read through each hospital's own memory graph. */
async function renderPlantAlerts(fei) {
  const box = $("#plant-alerts");
  box.innerHTML = "";
  try {
    const ev = await api(`/api/event/${encodeURIComponent(fei)}`);
    ev.alerts.forEach((a) => {
      const hit = a.stocked_drugs_made_there.length || a.substitutes_you_chose_made_there.length;
      const card = el("div", { class: `alert-card${hit ? " hit" : ""}` });
      card.append(el("h3", {}, a.name));
      if (!hit) card.append(el("p", {}, "Nothing it stocks comes from this plant."));
      if (a.stocked_drugs_made_there.length) {
        card.append(el("p", { class: "hitline" }, `Stocks from this plant: ${a.stocked_drugs_made_there.map(title).join(", ")}`));
      }
      a.substitutes_you_chose_made_there.forEach((d) => card.append(
        el("p", { class: "hitline" }, `Its earlier switch of ${title(d.drug)} to ${title(d.substitute)} depends on this plant.`)));
      box.append(card);
    });
  } catch (e) {
    box.append(el("p", { class: "state error" }, `Could not read hospital memory: ${e.message}`));
  }
}

/* Risk view, second table: betweenness centrality from FalkorDB's algo.betweenness. */
async function loadBridges() {
  const state = $("#bridge-state");
  try {
    const res = await api("/api/bridges?limit=15");
    const tbody = $("#bridge-table tbody");
    tbody.innerHTML = "";
    res.rows.forEach((r) => {
      const tr = el("tr");
      const plant = el("td");
      plant.append(plantLink(r.plant, r.fei), " ", el("span", { class: "code" }, `FEI ${r.fei}`));
      tr.append(el("td", { class: "num" }, String(r.betweenness)), plant, el("td", {}, r.country),
        el("td", { class: "num" }, `${r.drugs_made} / ${r.classes}`), joinNodes(el("td"), r.sample.map(drugLink), ""));
      tbody.append(tr);
    });
    $("#bridge-cypher").textContent = res.cypher;
    setState(state, res.rows.length ? "" : "No plant bridges two drug supply chains in this graph.");
    $("#bridge-table").hidden = !res.rows.length;
  } catch (e) {
    setState(state, `Could not run betweenness: ${e.message}`, true);
  }
}

loadHospitals();
