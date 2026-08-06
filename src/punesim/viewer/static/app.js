/* Pune Sim viewer — vanilla ES module, no build step.
   Map: Leaflet 1.9.4 + CARTO dark raster (V0 fallback per research;
   swap initMap() for MapLibre+PMTiles at V3 — nothing else changes). */

const $ = (s) => document.querySelector(s);
const DAY = 86400;

const state = {
  meta: null, people: [], places: [], scenes: [], ticker: [],
  byId: new Map(),
  day: 0, t: 8 * 3600, playing: false,
  selected: null,
  map: null, markers: new Map(), pulses: [],
  posTimer: null,
};

/* ---------- helpers ---------- */
function hue(id) { let h = 0; for (const c of id) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h % 360; }
function avatarColor(id) { return `hsl(${hue(id)} 55% 62%)`; }
function initials(name) {
  const p = name.trim().split(/\s+/);
  return ((p[0]?.[0] || "?") + (p[1]?.[0] || "")).toUpperCase();
}
function hm(t) {
  const s = ((t % DAY) + DAY) % DAY;
  return `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
}
function dayName(d) {
  const names = ["Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed"]; // epoch 2026-01-01 = Thursday
  return `${names[d % 7]} · Day ${d}`;
}
function relTime(t, now) {
  const dt = now - t;
  if (dt < 0) return "later today";
  if (dt < 90) return "just now";
  if (dt < 3600) return `${Math.round(dt / 60)} min ago`;
  if (Math.floor(t / DAY) === Math.floor(now / DAY)) {
    return (t % DAY) < 12 * 3600 ? "this morning" : "earlier today";
  }
  if (Math.floor(now / DAY) - Math.floor(t / DAY) === 1) return "yesterday";
  return `${Math.floor(now / DAY) - Math.floor(t / DAY)} days ago`;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const KIND_COLOR = {
  "trip.start": "var(--trip)", "trip.end": "var(--trip)", "activity.start": "var(--trip)",
  "scene.morning": "var(--scene)", "scene.reaction": "var(--danger)",
  "mood.delta": "var(--mood)", "memory.formed": "var(--memory)",
  "message.sent": "var(--phone)", "conversation.held": "var(--scene)",
  "hazard.road.collision": "var(--danger)", "hospital.admitted": "var(--danger)",
  "ambulance.dispatched": "var(--danger)", "condition.set": "var(--danger)",
  "hazard.water.supply_cut": "var(--danger)", "hazard.power.outage": "var(--danger)",
  "hazard.fire.small": "var(--danger)", "info.heard": "var(--rumor)",
  "belief.action": "var(--rumor)", "plan.avoided": "var(--rumor)",
  "pressure.crossed": "var(--mood)", "info.rumor": "var(--rumor)",
  "hospital.discharged": "var(--danger)", "money.paid": "var(--mood)",
  "loan.taken": "var(--mood)", "loan.interest": "var(--mood)",
  "police.fir.registered": "var(--phone)", "fir.update": "var(--phone)",
  "crowd.gathered": "var(--danger)", "police.deployed": "var(--phone)",
  "curfew.imposed": "var(--danger)", "unrest.communal_tension": "var(--danger)",
};

/* Linkify known person names inside humanized sentences. */
let nameIndex = [];
function linkNames(text) {
  let out = esc(text);
  for (const [name, id] of nameIndex) {
    if (out.includes(name)) {
      out = out.split(name).join(`<a class="plink" data-pid="${id}" href="#">${name}</a>`);
    }
  }
  return out;
}

/* ---------- boot ---------- */
async function boot() {
  const [meta, people, places, scenes, ticker, rumors] = await Promise.all(
    ["/api/meta", "/api/people", "/api/places", "/api/scenes", "/api/ticker", "/api/rumors"].map((u) =>
      fetch(u).then((r) => r.json())));
  Object.assign(state, { meta, people, places, scenes, ticker, rumors });
  for (const p of people) state.byId.set(p.id, p);
  nameIndex = people.map((p) => [p.name, p.id]).sort((a, b) => b[0].length - a[0].length).slice(0, 400);

  state.day = Math.min(1, meta.days - 1);
  state.t = state.day * DAY + 7 * 3600;

  $("#meta").textContent =
    `${meta.people} people · ${meta.events} events · seed ${meta.seed} · #${meta.hash.slice(0, 8)}`;
  initMap();
  renderRail();
  renderTicker();
  renderScenesPanel();
  renderRumorsPanel();
  renderInjectPanel();
  wireControls();
  updateClock();
  refreshPositions();
  drawTimeline();
}

/* ---------- map ---------- */
function initMap() {
  const b = state.meta.bounds;
  const map = L.map("map", { zoomControl: false, attributionControl: true })
    .fitBounds([[b[0][0], b[0][1]], [b[1][0], b[1][1]]]);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19,
  }).addTo(map);
  for (const pl of state.places) {
    if (!pl.name) continue;
    const icon = { temple: "🛕", mosque: "🕌", church: "⛪", school: "🏫", hospital: "🏥",
      clinic: "🩺", police: "🚓", bank: "🏦", shop: "🛒", market: "🛒", restaurant: "🍽",
      office: "📮", venue: "🎪" }[pl.kind] || "📍";
    L.marker([pl.lat, pl.lon], {
      icon: L.divIcon({ className: "poi-label", html: `${icon} ${esc(pl.name)}`, iconSize: null }),
      interactive: false, opacity: 0.85,
    }).addTo(map);
  }
  state.map = map;
}

async function refreshPositions() {
  const t = Math.round(state.day * DAY + state.t % DAY);
  const pos = await fetch(`/api/positions?t=${t}`).then((r) => r.json());
  const seen = new Set();
  for (const p of pos) {
    seen.add(p.id);
    let m = state.markers.get(p.id);
    const sel = state.selected === p.id;
    if (!m) {
      m = L.marker([p.lat, p.lon], {
        icon: L.divIcon({
          className: `dot${sel ? " sel" : ""}`, iconSize: [11, 11],
          html: `<div style="width:11px;height:11px;border-radius:50%;background:${avatarColor(p.id)}"></div>`,
        }),
        title: p.name, riseOnHover: true,
      }).addTo(state.map).on("click", () => selectPerson(p.id));
      state.markers.set(p.id, m);
    } else {
      m.setLatLng([p.lat, p.lon]);
      m.getElement()?.classList.toggle("sel", sel);
    }
    m.setTooltipContent?.(null);
    m.unbindTooltip();
    m.bindTooltip(`${p.name} — ${p.activity || p.state}${p.at_name ? " @ " + p.at_name : ""}`,
      { direction: "top", offset: [0, -6] });
    state._lastPos = state._lastPos || new Map();
    state._lastPos.set(p.id, p);
  }
  updateRailStatus();
  updatePulses(t);
}

function updatePulses(t) {
  for (const pu of state.pulses) pu.remove();
  state.pulses = [];
  for (const ev of state.ticker) {
    if (ev.provenance !== "user" || !ev.place) continue;
    if (t >= ev.t && t <= ev.t + 45 * 60) {
      const pl = state.places.find((p) => p.id === ev.place);
      if (!pl) continue;
      const m = L.marker([pl.lat, pl.lon], {
        icon: L.divIcon({ className: "pulse", iconSize: [46, 46] }), interactive: false,
      }).addTo(state.map);
      state.pulses.push(m);
    }
  }
}

/* ---------- rail ---------- */
function renderRail(filter = "") {
  const q = filter.toLowerCase();
  const hurt = new Set(
    state.ticker.filter((e) => e.type === "hospital.admitted")
      .map((e) => (e.text.match(/^(.+?) admitted/) || [])[1]).filter(Boolean));
  $("#people").innerHTML = state.people
    .filter((p) => !q || p.name.toLowerCase().includes(q) || p.occupation.includes(q) || p.household.includes(q))
    .map((p) => `
      <div class="person-row${state.selected === p.id ? " sel" : ""}${hurt.has(p.name) ? " hurt" : ""}" data-pid="${p.id}">
        <div class="avatar" style="background:${avatarColor(p.id)}">${initials(p.name)}</div>
        <div class="who">
          <div class="nm">${esc(p.name)}</div>
          <div class="st" data-st="${p.id}">${p.age} · ${esc(p.occupation)}</div>
        </div>
      </div>`).join("");
}

function updateRailStatus() {
  if (!state._lastPos) return;
  for (const el of document.querySelectorAll("[data-st]")) {
    const p = state._lastPos.get(el.dataset.st);
    if (p) el.textContent = p.activity ? `${p.activity}${p.at_name ? " @ " + p.at_name : ""}`
      : (p.state === "transit" ? `walking → ${p.at_name}` : `at ${p.at_name || "home"}`);
  }
}

/* ---------- ticker ---------- */
function renderTicker() {
  const now = state.day * DAY + (state.t % DAY);
  const rows = state.ticker
    .filter((e) => e.t <= now && !["memory.formed", "mood.delta", "plan.revised", "condition.set"].includes(e.type))
    .slice(-40).reverse();
  $("#tickerinner").innerHTML = rows.map((e) => `
    <div class="tick${e.provenance === "user" ? " injected" : ""}" data-seq="${e.seq}"
         title="${e.provenance === "user" ? "injected event — click for consequences" : esc(e.hm)}">
      <b>${esc(e.hm)}</b> — ${linkNames(e.text)}</div>`).join("");
}

/* ---------- consequence cone ---------- */
function showEventCone(seq) {
  const root = state.ticker.find((e) => e.seq === seq);
  if (!root) return;
  const children = new Map();
  for (const e of state.ticker) {
    if (e.caused_by != null) {
      if (!children.has(e.caused_by)) children.set(e.caused_by, []);
      children.get(e.caused_by).push(e);
    }
  }
  const cone = [];
  const stack = [seq];
  while (stack.length) {
    for (const c of children.get(stack.pop()) || []) { cone.push(c); stack.push(c.seq); }
  }
  cone.sort((a, b) => a.t - b.t);
  document.querySelector('[data-tab="person"]').click();
  $("#panel-person").innerHTML = `
    <div class="idcard">
      <div class="avatar" style="background:var(--danger)">⚡</div>
      <div><h2>Injected event</h2><div class="sub">${esc(root.hm)} — ${linkNames(root.text)}</div></div>
    </div>
    <details open><summary>Consequence cone (${cone.length} downstream events)</summary>
      <div class="body">${cone.map((e) => `
        <div class="evline notable">
          <span class="k" style="background:${KIND_COLOR[e.type] || "var(--fg-faint)"}"></span>
          <span class="t">${esc(e.hm)}</span>
          <span class="txt">${linkNames(e.text)}</span>
        </div>`).join("") || '<p class="hint">no linked consequences recorded</p>'}</div>
    </details>
    <p class="hint">Scenes triggered by this event appear in the Scenes tab (⚡ Reaction).</p>`;
}

/* ---------- timeline ---------- */
function drawTimeline() {
  const cv = $("#tl-canvas");
  const w = (cv.width = cv.clientWidth * devicePixelRatio);
  const h = (cv.height = cv.clientHeight * devicePixelRatio);
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  const d0 = state.day * DAY, d1 = d0 + DAY;
  const x = (t) => ((t - d0) / DAY) * w;

  // phase shading
  ctx.fillStyle = "rgba(255,255,255,0.03)";
  ctx.fillRect(x(d0 + 6 * 3600), 0, x(d0 + 10 * 3600) - x(d0 + 6 * 3600), h);
  ctx.fillRect(x(d0 + 17 * 3600), 0, x(d0 + 21 * 3600) - x(d0 + 17 * 3600), h);

  // city track: ticker density dots (upper half)
  for (const e of state.ticker) {
    if (e.t < d0 || e.t >= d1) continue;
    const c = e.provenance === "user" ? "#ef4444" : "#4a9eff";
    ctx.fillStyle = c;
    ctx.globalAlpha = e.provenance === "user" ? 1 : 0.55;
    const r = e.provenance === "user" ? 4 * devicePixelRatio : 2 * devicePixelRatio;
    ctx.beginPath(); ctx.arc(x(e.t), h * 0.28, r, 0, 7); ctx.fill();
    if (e.provenance === "user") { ctx.fillRect(x(e.t) - devicePixelRatio, 0, 2 * devicePixelRatio, h); }
  }
  ctx.globalAlpha = 1;

  // scenes (middle)
  for (const s of state.scenes) {
    if (s.t < d0 || s.t >= d1) continue;
    ctx.fillStyle = s.kind === "scene.reaction" ? "#ef4444" : "#a78bfa";
    ctx.beginPath(); ctx.arc(x(s.t), h * 0.55, 3.5 * devicePixelRatio, 0, 7); ctx.fill();
  }

  // selected person's day (lower)
  if (state._personDay) {
    ctx.fillStyle = "#e8a33d";
    for (const ev of state._personDay) {
      if (ev.t < d0 || ev.t >= d1) continue;
      ctx.globalAlpha = ev.routine ? 0.5 : 1;
      ctx.beginPath(); ctx.arc(x(ev.t), h * 0.82, 2.5 * devicePixelRatio, 0, 7); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  positionCursor();
  const labels = ["night", "morning", "day", "evening"];
  $("#tl-phases").innerHTML =
    [0, 6, 10, 17].map((hh, i) => {
      const next = [6, 10, 17, 24][i];
      return `<div style="flex:${next - hh}">${labels[i]} </div>`;
    }).join("");
}

function positionCursor() {
  const frac = (state.t % DAY) / DAY;
  $("#tl-cursor").style.left = `calc(${(frac * 100).toFixed(3)}% - 1px)`;
}

/* ---------- inspector: person ---------- */
async function selectPerson(pid) {
  state.selected = pid;
  history.replaceState(null, "", `#${pid}`);
  renderRail($("#search").value);
  document.querySelector('[data-tab="person"]').click();
  const d = await fetch(`/api/person/${pid}`).then((r) => r.json());
  state._personDay = d.timeline;
  drawTimeline();
  const pos = state._lastPos?.get(pid);
  if (pos) state.map.panTo([pos.lat, pos.lon]);
  const now = state.day * DAY + (state.t % DAY);

  const days = [...new Set(d.timeline.map((e) => e.day))].sort((a, b) => a - b);
  const dayBlocks = days.map((dd) => {
    const evs = d.timeline.filter((e) => e.day === dd);
    const notable = evs.filter((e) => !e.routine).length;
    return `
    <details ${dd === state.day ? "open" : ""}>
      <summary>${dayName(dd)} <span style="margin-left:auto;color:var(--fg-faint)">${evs.length} events${notable ? ` · <span style="color:var(--accent)">${notable} notable</span>` : ""}</span></summary>
      <div class="body">
        ${evs.map((e) => `
          <div class="evline${e.routine ? "" : " notable"}">
            <span class="k" style="background:${KIND_COLOR[e.type] || "var(--fg-faint)"}"></span>
            <span class="t" title="${relTime(e.t, now)}">${e.hm}</span>
            <span class="txt">${linkNames(e.text)}</span>
          </div>`).join("")}
      </div>
    </details>`;
  }).join("");

  const moodsum = d.moods.reduce((a, m) => a + m.delta, 0);
  $("#panel-person").innerHTML = `
    <div class="idcard">
      <div class="avatar" style="background:${avatarColor(d.id)}">${initials(d.name)}</div>
      <div>
        <h2>${esc(d.name)}</h2>
        <div class="sub">${d.age} · ${esc(d.occupation)} · ${esc(d.household)}</div>
        <div class="sub">home: ${esc(d.home_name)}${d.work_name ? " · goes to: " + esc(d.work_name) : ""}</div>
      </div>
    </div>
    <div class="chips">
      ${d.members.filter((m) => m.id !== d.id).map((m) =>
        `<span class="chip plink" data-pid="${m.id}">${esc(m.name)} (${m.age})</span>`).join("")}
    </div>

    <details open><summary>Their days</summary><div class="body">${dayBlocks || '<p class="hint">nothing yet</p>'}</div></details>

    <details ${d.moods.length ? "open" : ""}><summary>Mood ledger
      <span style="margin-left:auto" class="${moodsum >= 0 ? "mood-pos" : "mood-neg"}">${moodsum >= 0 ? "+" : ""}${moodsum.toFixed(1)}</span></summary>
      <div class="body">${d.moods.map((m) => `
        <div class="evline"><span class="t">${hm(m.t)}</span>
          <span class="txt ${m.delta >= 0 ? "mood-pos" : "mood-neg"}">${m.dim} ${m.delta >= 0 ? "+" : ""}${m.delta}</span></div>`).join("") || '<p class="hint">steady</p>'}</div>
    </details>

    <details ${d.memories.length ? "open" : ""}><summary>Memories (${d.memories.length})</summary>
      <div class="body">${d.memories.map((m) => `
        <div class="memory" title="salience ${m.salience}">“${esc(m.summary)}” <span style="color:var(--fg-faint)">— ${relTime(m.t, now)}</span></div>`).join("") || '<p class="hint">none yet</p>'}</div>
    </details>

    ${d.heard.length ? `
    <details open><summary>What they've heard (${d.heard.length})</summary>
      <div class="body">${d.heard.map((hh) => `
        <div class="heardline" title="hop ${hh.hop}${hh.ops.length ? " · drifted: " + hh.ops.join(", ").toLowerCase() : ""}">
          <div class="credbar"><div style="width:${Math.round((hh.credence || 0) * 100)}%"></div></div>
          <div class="heardtxt">“${esc(hh.text)}”
            <span style="color:var(--fg-faint)">— ${hh.channel === "witness" ? "saw it" : "from " + esc(hh.source)}, ${relTime(hh.t, now)} · believes ${Math.round((hh.credence || 0) * 100)}%</span></div>
        </div>`).join("")}</div>
    </details>` : ""}

    <details ${d.interviews.length || state.meta.llm ? "open" : ""}><summary>Interviews (${d.interviews.length})</summary>
      <div class="body">${d.interviews.map((iv) => `
        <div class="qa">
          <div class="q">“${esc(iv.question)}” <span style="color:var(--fg-faint)">— a journalist, ${esc(iv.hm)}</span></div>
          <div class="a">${esc(iv.answer)}</div>
        </div>`).join("")}
        ${state.meta.llm ? `
        <div class="askrow">
          <input id="ask-input" type="text" placeholder="ask ${esc(d.name.split(" ")[0])} something…">
          <button id="ask-go" class="primary">Ask</button>
        </div>
        <div id="ask-status" class="formhint"></div>` : ""}
      </div>
    </details>

    <details><summary>Raw dossier</summary><div class="body raw">${esc(JSON.stringify(
      { id: d.id, religion: d.religion, home: d.home, work: d.work }, null, 1))}</div></details>
  `;
  const askBtn = $("#ask-go");
  if (askBtn) {
    askBtn.onclick = () => askPerson(d.id);
    $("#ask-input").addEventListener("keydown", (e) => { if (e.key === "Enter") askPerson(d.id); });
  }
}

/* ---------- inspector: scenes ---------- */
function renderScenesPanel() {
  $("#panel-scenes").innerHTML = state.scenes.slice().reverse().map((s) => `
    <details class="scenecard${s.kind === "scene.reaction" ? " reaction" : ""}">
      <summary>${s.kind === "scene.reaction" ? "⚡ Reaction" : "Morning"} — ${esc(s.family)} family
        <span class="when">${esc(s.hm)}</span></summary>
      <div class="body">
        ${s.narration ? `<div class="narration">${esc(s.narration)}</div>` : ""}
        ${(s.transcript || "").split("\n").filter(Boolean).map((line) => {
          const m = line.match(/^([^:]{2,40}):\s*(.*)$/);
          if (!m) return `<div class="narration">${esc(line)}</div>`;
          let speaker = m[1].trim();
          if (/^person:/.test(speaker)) {           // old logs used ids as labels
            const known = state.byId.get(speaker);
            if (known) speaker = known.name.split(" ")[0];
          }
          const pid = (state.people.find((p) => p.name.startsWith(speaker.split(" ")[0]) ) || {}).id || speaker;
          return `
            <div class="bubblewrap">
              <div class="avatar" style="background:${avatarColor(pid)};width:22px;height:22px;flex:0 0 22px;font-size:.6em">${initials(speaker)}</div>
              <div class="bubble"><div class="spk" style="color:${avatarColor(pid)}">${esc(speaker)}</div>${esc(m[2])}</div>
            </div>`;
        }).join("")}
      </div>
    </details>`).join("") || '<p class="hint">no scenes in this log — run with --scenes</p>';
}

/* ---------- inspector: rumors ---------- */
function renderRumorsPanel() {
  const VER = { true: ["true", "ver-true"], false: ["false", "ver-false"],
    distorted: ["distorted", "ver-dist"], unknown: ["unverified", "ver-unk"] };
  $("#panel-rumors").innerHTML = (state.rumors || []).slice().reverse().map((r) => {
    const [vlabel, vcls] = VER[r.veracity] || VER.unknown;
    const maxN = Math.max(1, ...r.by_day.map((d) => d.n));
    const spark = r.by_day.map((d) =>
      `<div class="spark-bar" title="day ${d.day}: heard ${d.n}×" style="height:${Math.max(3, (d.n / maxN) * 26)}px"></div>`).join("");
    const origin = r.origin_prov === "user" ? "injected"
      : r.origin_type && r.origin_type.startsWith("hazard.") ? "a real incident" : "word of mouth";
    return `
    <details class="rumorcard" open>
      <summary><span class="ver ${vcls}">${vlabel}</span> “${esc(r.variants[0]?.text || r.key)}”
        <span class="when">${esc(r.first_hm)}</span></summary>
      <div class="body">
        <div class="rumor-stats">
          <span title="unique people who heard any version">👂 ${r.reach} heard</span>
          <span title="credence ≥ 55% at last hearing">🧠 ${r.believers} believe</span>
          <span title="how it started">🌱 ${origin}</span>
          <div class="spark" title="hearings per day">${spark}</div>
        </div>
        ${r.variants.length > 1 ? `
        <div class="drift">
          <div class="drift-title">How the story changed</div>
          ${r.variants.map((v, i) => `
            <div class="drift-row">
              <span class="hopn">${i === 0 ? "origin" : "hop " + v.hop}</span>
              <span class="txt">“${esc(v.text)}”</span>
              ${v.ops.length ? `<span class="ops">${v.ops.map((o) => `<i>${esc(o.toLowerCase())}</i>`).join("")}</span>` : ""}
            </div>`).join("")}
        </div>` : ""}
        ${r.actions.length ? `
        <div class="drift">
          <div class="drift-title">Acting on it</div>
          ${r.actions.map((a) => `
            <div class="evline notable"><span class="k" style="background:var(--danger)"></span>
              <span class="t">${esc(a.hm)}</span>
              <span class="txt"><a class="plink" data-pid="${a.person_id}" href="#">${esc(a.person)}</a>
                ${a.action === "store_water" ? "stores water — avoiding" : a.action === "stop_patronage" ? "stops going to" : "now avoids"} ${esc(a.place)}</span></div>`).join("")}
        </div>` : ""}
        <details><summary>Every hearing (${r.spread.length})</summary>
          <div class="body">${r.spread.map((s) => `
            <div class="evline">
              <span class="t">${esc(s.hm)}</span>
              <span class="txt"><a class="plink" data-pid="${s.person_id}" href="#">${esc(s.person)}</a>
                ${s.channel === "witness" ? "saw it" : s.channel === "household" ? `heard at home from ${esc(s.source)}` : `heard from ${esc(s.source)}`}
                <span class="cred" title="belief after hearing">${Math.round((s.credence || 0) * 100)}%</span></span>
            </div>`).join("")}</div>
        </details>
      </div>
    </details>`;
  }).join("") || '<p class="hint">no rumors yet — inject one (type "info.rumor") or let a hazard start one</p>';
}

/* ---------- inspector: inject ---------- */
function renderInjectPanel() {
  if (!state.meta.llm) {
    $("#panel-inject").innerHTML =
      '<p class="hint">No LLM key configured — restart <code>punesim serve</code> with a .env key to compile injections.</p>';
    return;
  }
  $("#panel-inject").innerHTML = `
    <div class="injectform">
      <p class="formhint">Describe anything. It compiles into a grounded event — real places,
      real residents, validated before it can touch the world.</p>
      <textarea id="inj-text" rows="4" placeholder="e.g. the city DM was killed in broad daylight near Shaniwar Wada at noon on day 2&#10;e.g. a rumor spreads that the mandal treasurer stole two lakh rupees"></textarea>
      <div class="formrow">
        <label>default day <input id="inj-day" type="number" min="0" value="0"></label>
        <button id="inj-go" class="primary">Compile</button>
      </div>
      <div id="inj-result"></div>
    </div>`;
  $("#inj-go").onclick = async () => {
    const text = $("#inj-text").value.trim();
    if (!text) return;
    const res = $("#inj-result");
    res.innerHTML = '<p class="hint">compiling against the world card…</p>';
    $("#inj-go").disabled = true;
    try {
      const r = await fetch("/api/compile", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, day: Number($("#inj-day").value) || 0 }),
      }).then((x) => x.json());
      if (r.error) {
        res.innerHTML = `<div class="compileerr">✗ ${esc(r.error)}${
          (r.details || []).map((d) => `<div>- ${esc(d)}</div>`).join("")}</div>`;
      } else {
        res.innerHTML = `
          <div class="compiled">
            <div class="drift-title">Compiled &amp; validated</div>
            <pre class="preview">${esc(r.preview)}</pre>
            <div class="drift-title">Saved to ${esc(r.saved)} (${r.count} injection${r.count > 1 ? "s" : ""}) — run it:</div>
            <pre class="runcmd" title="click to copy">${esc(r.run_cmd)}</pre>
            <p class="formhint">Injections belong to runs: this never rewrites the log you are viewing.
            Run the command in a terminal, then serve the new db to watch it play out.</p>
          </div>`;
        res.querySelector(".runcmd").onclick = (ev) =>
          navigator.clipboard?.writeText(ev.target.textContent).then(() => {
            ev.target.classList.add("copied"); setTimeout(() => ev.target.classList.remove("copied"), 900);
          });
      }
    } catch (e) {
      res.innerHTML = `<div class="compileerr">✗ ${esc(String(e))}</div>`;
    }
    $("#inj-go").disabled = false;
  };
}

/* ---------- ask-them-something ---------- */
async function askPerson(pid) {
  const input = $("#ask-input");
  const q = input.value.trim();
  if (!q) return;
  const btn = $("#ask-go");
  btn.disabled = true; btn.textContent = "…";
  const box = $("#ask-status");
  box.textContent = "the clock pauses; they consider the question…";
  try {
    const r = await fetch("/api/interview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person_id: pid, question: q }),
    }).then((x) => x.json());
    if (r.error) { box.textContent = "✗ " + r.error; }
    else { await selectPerson(pid); return; }   // dossier re-renders with the new Q/A
  } catch (e) { box.textContent = "✗ " + e; }
  btn.disabled = false; btn.textContent = "Ask";
}

/* ---------- controls ---------- */
function updateClock() {
  $("#clock").textContent = hm(state.t);
  $("#daylabel").textContent = dayName(state.day);
}

function setTime(t, day = state.day) {
  state.day = day;
  state.t = day * DAY + (((t % DAY) + DAY) % DAY);
  updateClock();
  positionCursor();
  renderTicker();
  clearTimeout(state.posTimer);
  state.posTimer = setTimeout(refreshPositions, 120);
}

function wireControls() {
  $("#prevday").onclick = () => { if (state.day > 0) { state.day--; setTime(state.t, state.day); drawTimeline(); } };
  $("#nextday").onclick = () => {
    if (state.day < state.meta.days - 1) { state.day++; setTime(state.t, state.day); drawTimeline(); } };
  $("#playbtn").onclick = () => {
    state.playing = !state.playing;
    $("#playbtn").textContent = state.playing ? "⏸" : "▶";
    if (state.playing) tickPlay();
  };
  const tl = $("#timeline");
  const scrub = (ev) => {
    const r = tl.getBoundingClientRect();
    const frac = Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1);
    setTime(frac * DAY);
  };
  tl.addEventListener("mousedown", (ev) => {
    scrub(ev);
    const mv = (e2) => scrub(e2);
    const up = () => { removeEventListener("mousemove", mv); removeEventListener("mouseup", up); };
    addEventListener("mousemove", mv); addEventListener("mouseup", up);
  });
  $("#search").addEventListener("input", (e) => renderRail(e.target.value));
  document.body.addEventListener("click", (e) => {
    const a = e.target.closest(".plink, .person-row");
    if (a) { e.preventDefault(); selectPerson(a.dataset.pid); return; }
    const tick = e.target.closest(".tick.injected");
    if (tick) { showEventCone(Number(tick.dataset.seq)); return; }
    const tab = e.target.closest(".tab");
    if (tab) {
      document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === tab));
      document.querySelectorAll(".panel").forEach((x) =>
        x.classList.toggle("active", x.id === `panel-${tab.dataset.tab}`));
      if (tab.dataset.tab === "scenes") history.replaceState(null, "", "#scenes");
      if (tab.dataset.tab === "rumors") history.replaceState(null, "", "#rumors");
      if (tab.dataset.tab === "inject") history.replaceState(null, "", "#inject");
    }
  });
  addEventListener("resize", drawTimeline);
  // deep links: #person:000.1 opens a dossier, #scenes / #rumors / #inject open panels
  const h = decodeURIComponent(location.hash.slice(1));
  if (h === "scenes") document.querySelector('[data-tab="scenes"]').click();
  else if (h === "rumors") document.querySelector('[data-tab="rumors"]').click();
  else if (h === "inject") document.querySelector('[data-tab="inject"]').click();
  else if (h.startsWith("person:")) selectPerson(h);
}

function tickPlay() {
  if (!state.playing) return;
  setTime((state.t % DAY) + 60);           // 1 sim-minute per wall ~90ms
  if ((state.t % DAY) < 60) {              // wrapped past midnight
    if (state.day < state.meta.days - 1) { state.day++; drawTimeline(); }
    else { state.playing = false; $("#playbtn").textContent = "▶"; return; }
  }
  setTimeout(tickPlay, 90);
}

boot();
