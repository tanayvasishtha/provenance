/* Ask the agent, and each hospital's memory graph. Uses $, api, el, title, setState from app.js. */

const MODE_TEXT = {
  live: "Answered by Claude Opus 5.5, calling the graph tools below.",
  replay: "Recorded answer from Claude Opus 5.5, replayed so the demo runs without an API key. The tool calls below ran live against the graph.",
  "graph-only": "No model key set, so a rule-based planner ran the same graph tools and filled a template.",
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

$("#ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const question = $("#question").value.trim(), hospital = $("#hospital").value;
  if (question.length < 3) return;
  const btn = $("#ask-form button[type=submit]"), state = $("#ask-state");
  btn.disabled = true;
  $("#ask-result").hidden = true;
  setState(state, "The agent is querying the graph...");
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, hospital }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const r = await res.json();
    $("#ask-mode").innerHTML = "";
    $("#ask-mode").append(el("span", { class: "code" }, `mode: ${r.mode}`), ` ${MODE_TEXT[r.mode] || ""}`);
    $("#ask-answer").textContent = r.answer;
    const ol = $("#ask-trace");
    ol.innerHTML = "";
    if (!r.trace.length) ol.append(el("li", {}, "No graph query was needed for this answer."));
    r.trace.forEach((t) => {
      const li = el("li");
      li.append(el("span", { class: "tool" }, t.tool), " ", el("span", { class: "args" }, JSON.stringify(t.input)),
        ` returned ${t.rows} row${t.rows === 1 ? "" : "s"}`);
      const d = el("details");
      d.append(el("summary", {}, "Cypher"), el("pre", {}, t.cypher));
      li.append(d);
      ol.append(li);
    });
    setState(state, "");
    $("#ask-result").hidden = false;
    renderMemory(hospital);
  } catch (e) {
    setState(state, `The agent could not answer: ${e.message}`, true);
  } finally {
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

loadHospitals();
