/* LeadHub frontend — same API and workflow as before, rebuilt CSP-safe
   (no inline handlers; event delegation) and XSS-safe (everything escaped,
   status class from a fixed map, names in data-* not onclick). */
"use strict";
let ME = null, BOOT = null, ROWS = [], CUR = null, TOKEN = null, PENDING = null;
let VIEW = "leads", SYNCT = null, RSTU = null, TOTAL = 0, DAYSHOWN = "", FUMODE = false, FUSTATS = null;
let BK_SEL = null;
const DV = { level: "months", month: null, day: null, agent: "" };

const $ = s => document.querySelector(s);
const esc = s => (s == null ? "" : String(s)).replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ini = n => (n || "?").trim().slice(0, 1).toUpperCase();
function toast(m, ok) {
  const t = $("#toast");
  t.innerHTML = (ok !== false ? `<svg class="i sm"><use href="#ic-check"/></svg>` : "") +
    `<span>${esc(m)}</span>`;
  t.classList.add("on"); setTimeout(() => t.classList.remove("on"), 1900);
}

/* ---- status helpers (fixed maps; never interpolate raw status into markup) ---- */
const ST_CLASS = { "New": "s-New", "Attempted": "s-Attempted", "Contacted": "s-Contacted",
  "Interested": "s-Interested", "Not Interested": "s-NotInterested", "DNC": "s-DNC", "Closed": "s-Closed" };
const ST_ICON = { "Attempted": "ic-clock", "Contacted": "ic-phone", "Interested": "ic-star",
  "Not Interested": "ic-x", "DNC": "ic-ban", "Closed": "ic-check" };
function pill(s) {
  const cls = ST_CLASS[s] || "s-New", ic = ST_ICON[s];
  return `<span class="pill ${cls}${ic ? "" : " dot"}">` +
    (ic ? `<svg><use href="#${ic}"/></svg>` : "") + `${esc(s)}</span>`;
}

async function api(p, opt = {}) {
  opt.headers = Object.assign({ "Content-Type": "application/json", "X-Token": TOKEN || "" }, opt.headers || {});
  let r;
  try { r = await fetch(p, opt); } catch (e) { return { error: "Network error" }; }
  if (r.status === 401 && !p.includes("/api/login")) { signedOut(); return { error: "Session expired" }; }
  try { return await r.json(); } catch (e) { return { error: "Bad response" }; }
}
function signedOut() {
  TOKEN = null; ME = null;
  try { localStorage.removeItem("leadhub_token"); localStorage.removeItem("leadhub_user"); } catch (e) {}
  $("#app").classList.add("hide"); $("#login").classList.remove("hide"); backToUsers();
}
function e164(p) {
  if (!p) return ""; const d = String(p).replace(/\D/g, "");
  if (d.length === 10) return "+1" + d; if (d.length === 11 && d[0] === "1") return "+" + d;
  return d.length >= 11 ? "+" + d : "";
}

/* ---- carrier local-time (TCPA window 8am-9pm) ---- */
const TZ = {
  CT: "America/New_York", DE: "America/New_York", FL: "America/New_York", GA: "America/New_York",
  IN: "America/New_York", ME: "America/New_York", MD: "America/New_York", MA: "America/New_York",
  MI: "America/New_York", NH: "America/New_York", NJ: "America/New_York", NY: "America/New_York",
  NC: "America/New_York", OH: "America/New_York", PA: "America/New_York", RI: "America/New_York",
  SC: "America/New_York", VT: "America/New_York", VA: "America/New_York", WV: "America/New_York",
  DC: "America/New_York", PR: "America/Puerto_Rico", VI: "America/Puerto_Rico",
  AL: "America/Chicago", AR: "America/Chicago", IL: "America/Chicago", IA: "America/Chicago",
  KS: "America/Chicago", KY: "America/New_York", LA: "America/Chicago", MN: "America/Chicago",
  MS: "America/Chicago", MO: "America/Chicago", NE: "America/Chicago", ND: "America/Chicago",
  OK: "America/Chicago", SD: "America/Chicago", TN: "America/Chicago", TX: "America/Chicago",
  WI: "America/Chicago", CO: "America/Denver", ID: "America/Denver", MT: "America/Denver",
  NM: "America/Denver", UT: "America/Denver", WY: "America/Denver", AZ: "America/Phoenix",
  CA: "America/Los_Angeles", NV: "America/Los_Angeles", OR: "America/Los_Angeles",
  WA: "America/Los_Angeles", AK: "America/Anchorage", HI: "Pacific/Honolulu"
};
function carrierClock(state) {
  const zone = TZ[String(state || "").toUpperCase()]; if (!zone) return null;
  try {
    const now = new Date();
    const time = new Intl.DateTimeFormat("en-US", { timeZone: zone, hour: "numeric", minute: "2-digit", hour12: true }).format(now);
    const hour = +new Intl.DateTimeFormat("en-US", { timeZone: zone, hour: "numeric", hour12: false }).format(now);
    return { time, hour, ok: hour >= 8 && hour < 21 };
  } catch (e) { return null; }
}
function localTimeBox(state) {
  const c = carrierClock(state); if (!c) return "";
  return c.ok
    ? `<div class="clock ok"><svg class="i sm"><use href="#ic-check"/></svg><span><b>${esc(c.time)}</b> where they are — fine to call</span></div>`
    : `<div class="clock no"><svg class="i sm"><use href="#ic-ban"/></svg><span><b>${esc(c.time)}</b> where they are — outside 8am–9pm, do not call yet</span></div>`;
}

/* ---- theme ---- */
function curTheme() {
  const t = document.documentElement.getAttribute("data-theme");
  if (t) return t;
  try { return matchMedia("(prefers-color-scheme:dark)").matches ? "dark" : "light"; } catch (e) { return "light"; }
}
function themeIcon() {
  const d = curTheme() === "dark";
  $("#btnTheme").innerHTML = `<svg class="i sm" style="display:inline;vertical-align:-3px"><use href="#ic-${d ? "sun" : "moon"}"/></svg>`;
}
function initTheme() {
  try { const s = localStorage.getItem("leadhub_theme"); if (s) document.documentElement.setAttribute("data-theme", s); } catch (e) {}
  themeIcon();
}
function toggleTheme() {
  const next = curTheme() === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("leadhub_theme", next); } catch (e) {}
  themeIcon();
}

/* ---- text size (readability) ---- */
const TEXT_LEVELS = ["normal", "large", "xl"];
function curText() { return document.documentElement.getAttribute("data-text") || "normal"; }
function applyText(v) {
  if (v && v !== "normal") document.documentElement.setAttribute("data-text", v);
  else document.documentElement.removeAttribute("data-text");
}
function initText() {
  try { const s = localStorage.getItem("leadhub_text"); if (s) applyText(s); } catch (e) {}
}
function cycleText() {
  const n = TEXT_LEVELS[(TEXT_LEVELS.indexOf(curText()) + 1) % TEXT_LEVELS.length];
  applyText(n);
  try { localStorage.setItem("leadhub_text", n); } catch (e) {}
  toast("Text size: " + ({ normal: "Normal", large: "Large", xl: "Largest" }[n]));
}

/* ---- boot / login ---- */
(async function () {
  initTheme();
  initText();
  BOOT = await api("/api/bootstrap");
  if (BOOT.brand && BOOT.brand.name) {
    $("#brandNameA").textContent = BOOT.brand.name; $("#brandNameB").textContent = BOOT.brand.name;
    document.title = BOOT.brand.name;
    if (BOOT.brand.accent) document.documentElement.style.setProperty("--accent", BOOT.brand.accent);
  }
  renderUserList();
  if (BOOT.demo && BOOT.demo_login) {
    const h = $("#demoHint");
    if (h) {
      h.innerHTML = `<b>Live demo.</b> Sign in as <b>${esc(BOOT.demo_login.name)}</b>, PIN <b>${esc(BOOT.demo_login.pin)}</b>. Sample data — not a real company.`;
      h.classList.remove("hide");
    }
  }
  let stored = null;
  try { stored = JSON.parse(localStorage.getItem("leadhub_user") || "null"); TOKEN = localStorage.getItem("leadhub_token"); } catch (e) {}
  if (TOKEN && stored) {
    const p = await api("/api/leads?limit=1");
    if (!p.error) start(stored); else { TOKEN = null; }
  }
})();

function renderUserList() {
  const users = (BOOT && BOOT.users) || [];
  if (!users.length) {
    $("#ulist").innerHTML = `<div class="note">Ask your admin for your account.</div>`; return;
  }
  $("#ulist").innerHTML = users.map(u =>
    `<button class="ubtn" data-act="pick" data-uid="${u.id}"><span class="avatar">${esc(ini(u.name))}</span>` +
    `<b>${esc(u.name)}</b><span class="tag">${esc(u.role)}</span></button>`).join("");
}
function pick(id) {
  PENDING = (BOOT.users || []).find(u => u.id === id); if (!PENDING) return;
  $("#pinName").textContent = PENDING.name; $("#pinRole").textContent = PENDING.role;
  $("#pinAvatar").textContent = ini(PENDING.name);
  $("#pinErr").textContent = ""; $("#pinBox").value = "";
  $("#step1").classList.add("hide"); $("#step2").classList.remove("hide");
  setTimeout(() => $("#pinBox").focus(), 60);
}
function backToUsers() {
  PENDING = null; $("#pinBox").value = ""; $("#pinErr").textContent = "";
  $("#step2").classList.add("hide"); $("#step1").classList.remove("hide");
}
async function doLogin() {
  const pin = $("#pinBox").value.trim();
  if (!/^\d{4,12}$/.test(pin)) { $("#pinErr").textContent = "Enter your PIN."; return; }
  const r = await api("/api/login", { method: "POST", body: JSON.stringify({ name: PENDING.name, pin }) });
  if (r.error) { $("#pinErr").textContent = r.error; $("#pinBox").value = ""; $("#pinBox").focus(); return; }
  TOKEN = r.token;
  try { localStorage.setItem("leadhub_token", TOKEN); localStorage.setItem("leadhub_user", JSON.stringify(r.user)); } catch (e) {}
  start(r.user);
}
function start(u) {
  ME = u;
  $("#login").classList.add("hide"); $("#app").classList.remove("hide");
  $("#uav").textContent = ini(u.name); $("#uname").textContent = u.name; $("#urole").textContent = u.role;
  document.querySelectorAll("#navWrap a").forEach(a => {
    a.classList.toggle("hide", a.dataset.roles.indexOf(u.role) < 0);
  });
  const leadsLbl = document.querySelector('[data-label="leads"]'); if (leadsLbl) leadsLbl.textContent = u.role === "admin" ? "Leads" : "My Leads";
  const dailyLbl = document.querySelector('[data-label="daily"]'); if (dailyLbl) dailyLbl.textContent = u.role === "admin" ? "Daily Folders" : "My Daily Folders";
  $("#fStatus").innerHTML = '<option value="">All statuses</option>' + (BOOT.statuses || []).map(s => `<option>${esc(s)}</option>`).join("");
  $("#fBucket").innerHTML = '<option value="">Any age</option>' + (BOOT.buckets || []).map(b => `<option value="${esc(b.key)}">${esc(b.label)}</option>`).join("");
  if (u.role !== "admin") {
    $("#fScope").value = "mine"; $("#fScope").disabled = true;
    $("#dateWrap").classList.remove("hide"); $("#fDate").value = localToday();
  } else {
    $("#fScope").value = "all"; $("#dateWrap").classList.add("hide"); $("#fDate").value = "";
  }
  refreshFollowupBadge(); setInterval(refreshFollowupBadge, 120000);
  go(u.role === "admin" ? "dash" : "leads");
}
async function logout() {
  await api("/api/logout", { method: "POST", body: "{}" });
  try { localStorage.removeItem("leadhub_token"); localStorage.removeItem("leadhub_user"); } catch (e) {}
  location.reload();
}

/* ---- nav ---- */
function go(v) {
  const isFu = v === "followups";
  FUMODE = isFu; VIEW = isFu ? "leads" : v;
  ["dash", "daily", "buckets", "leads", "team"].forEach(k => {
    const s = $("#v" + k[0].toUpperCase() + k.slice(1)); if (s) s.classList.toggle("hide", k !== VIEW);
  });
  document.querySelectorAll("#navWrap a").forEach(a => a.classList.toggle("on", a.dataset.view === v));
  $("#pageTitle").textContent = isFu ? "Follow-ups due"
    : { dash: "Dashboard", buckets: "Lead Groups", daily: "Daily Folders",
        leads: ME.role === "admin" ? "Leads" : "My Leads", team: "Team" }[v];
  $("#fuBar").classList.toggle("hide", !isFu);
  if (isFu) { $("#fDate").value = ""; $("#fStatus").value = ""; $("#q").value = ""; }
  else if (ME.role !== "admin" && !$("#fDate").value) { $("#fDate").value = localToday(); }
  refresh();
}
async function refreshFollowupBadge() {
  if (!ME) return;
  const c = await api("/api/followups/count"); if (c.error) return;
  const el = $("#fuCount"); if (!el) return;
  el.textContent = c.due || 0; el.classList.toggle("hide", !(c.due > 0)); FUSTATS = c;
}
function refresh() {
  if (VIEW === "leads") { load(); refreshFollowupBadge(); }
  if (VIEW === "dash") { loadStats(); pollSync(); }
  if (VIEW === "buckets") loadBuckets();
  if (VIEW === "daily") openMonths();
  if (VIEW === "team") loadTeam();
}

/* ---- daily folders ---- */
function dvAgentParam() { return DV.agent ? `&agent=${encodeURIComponent(DV.agent)}` : ""; }
function crumbs(parts) {
  $("#crumbs").innerHTML = parts.map((p, i) =>
    i === parts.length - 1 ? `<b>${esc(p.label)}</b>`
      : `<a data-act="${esc(p.act)}" data-arg="${esc(p.arg || "")}">${esc(p.label)}</a><span class="sepr">›</span>`).join(" ");
}
async function openMonths() {
  DV.level = "months"; DV.month = null; DV.day = null;
  const d = await api("/api/calendar/months?x=1" + dvAgentParam()); if (d.error) return;
  if (ME.role === "admin") {
    const sel = $("#dvAgent"); sel.classList.remove("hide");
    sel.innerHTML = `<option value="">All agents</option>` +
      d.agents.map(a => `<option value="${a.id}" ${String(a.id) === String(DV.agent) ? "selected" : ""}>${esc(a.name)}</option>`).join("");
  }
  crumbs([{ label: "All months" }]);
  $("#dvBody").innerHTML = d.months.length ? `<div class="folders">` + d.months.map(m => {
    const isNow = m.month === d.today;
    return `<div class="folder ${isNow ? "today" : ""} ${m.leads ? "" : "empty-f"}" data-act="days" data-arg="${esc(m.month)}">
      ${isNow ? '<span class="badge-today">THIS MONTH</span>' : ""}<div class="fi">📁</div>
      <div class="fn">${esc(m.label)}</div>
      <div class="fm">${m.leads ? `${m.leads.toLocaleString()} leads across ${m.days} day${m.days === 1 ? "" : "s"}` : "no leads assigned yet"}</div>
      <div class="fs">${m.leads ? `<span class="mini">${m.worked} worked</span><span class="mini">${m.calls} calls</span>` : ""}</div></div>`;
  }).join("") + `</div>` : `<div class="empty">No months yet — assign some leads and today's folder appears here.</div>`;
}
async function openDays(month) {
  DV.level = "days"; DV.month = month; DV.day = null;
  const d = await api(`/api/calendar/days?month=${encodeURIComponent(month)}` + dvAgentParam()); if (d.error) return;
  crumbs([{ label: "All months", act: "months" }, { label: d.label }]);
  $("#dvBody").innerHTML = d.days.length ? `<div class="folders">` + d.days.map(x => {
    const isNow = x.day === d.today;
    return `<div class="folder ${isNow ? "today" : ""} ${x.leads ? "" : "empty-f"}" data-act="day" data-arg="${esc(x.day)}">
      ${isNow ? '<span class="badge-today">TODAY</span>' : ""}<div class="fi">📂</div>
      <div class="fn">${esc(x.label)}</div>
      <div class="fm">${x.leads ? `${x.leads.toLocaleString()} leads assigned` : "nothing assigned yet"}</div>
      <div class="fs">${x.leads ? `<span class="mini">${x.worked} worked</span>${x.interested ? pill("Interested").replace(">Interested<", `>${x.interested} interested<`) : ""}<span class="mini">${x.calls} calls</span>` : ""}</div>
      <div class="fm" style="margin-top:6px">${x.agents.map(a => esc(a.name)).join(", ")}</div></div>`;
  }).join("") + `</div>` : `<div class="empty">Nothing was assigned in ${esc(d.label)}.</div>`;
}
async function openDay(day) {
  DV.level = "day"; DV.day = day;
  const d = await api(`/api/calendar/day?day=${encodeURIComponent(day)}` + dvAgentParam()); if (d.error) return;
  const forDay = (d.agents || []).filter(a => a.day === day);
  crumbs([{ label: "All months", act: "months" }, { label: CalMonthLabel(day.slice(0, 7)), act: "days", arg: day.slice(0, 7) }, { label: d.label }]);
  const st = Object.entries(d.by_status || {}).sort((a, b) => b[1] - a[1]);
  const total = st.reduce((t, [, v]) => t + v, 0);
  $("#dvBody").innerHTML = `
    <div class="stats" style="margin-bottom:14px">
      <div class="stat hi"><div class="n">${total.toLocaleString()}</div><div class="l">Assigned this day</div></div>
      ${st.slice(0, 5).map(([k, v]) => `<div class="stat"><div class="n">${v.toLocaleString()}</div><div class="l">${esc(k)}</div></div>`).join("")}</div>
    ${forDay.length ? `<div class="card mb"><h3>Who was given what</h3><div class="tbl-wrap"><table>
      <thead><tr><th>Agent</th><th class="r">Leads</th><th class="r">Worked</th><th class="r">Interested</th><th class="r">Calls</th><th></th></tr></thead>
      <tbody>${forDay.map(a => `<tr><td class="co"><b>${esc(a.agent)}</b></td><td class="r">${a.leads}</td><td class="r">${a.worked}</td>
        <td class="r">${a.interested}</td><td class="r">${a.calls}</td>
        <td class="r"><button class="btn sec sm" data-act="dayleads" data-day="${esc(day)}" data-agent="${a.agent_id || 0}">Open leads</button></td></tr>`).join("")}</tbody></table></div></div>`
      : `<div class="empty">Nothing assigned on this day yet.</div>`}
    <div id="dayLeads"></div>`;
  if (ME.role !== "admin" && forDay.length) openDayLeads(day, ME.id);
}
function CalMonthLabel(ym) { const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]; const [y, m] = ym.split("-"); return `${M[+m - 1]} ${y}`; }
async function openDayLeads(day, agentId) {
  const scope = ME.role === "admin" && agentId ? `agent:${agentId}` : "mine";
  const d = await api(`/api/leads?scope=${scope}&day=${encodeURIComponent(day)}&limit=300`); if (d.error) return;
  $("#dayLeads").innerHTML = `<div class="card"><h3>Leads assigned on ${esc(CalDayLabel(day))} <span class="sp"></span><span class="hint">${d.total} total</span></h3>
    <div class="listwrap"><table><thead><tr><th>Company</th><th>State</th><th class="r">Age</th><th>Status</th><th class="r">Calls</th></tr></thead>
    <tbody>${d.rows.map(r => `<tr><td class="co"><b>${esc(r.legal_name)}</b><span class="s">${esc(r.city)} · ${esc(r.docket)}</span></td>
      <td>${esc(r.state)}</td><td class="r">${r.age == null ? "—" : r.age + "d"}</td><td>${pill(r.status)}</td><td class="r">${r.call_attempts || 0}</td></tr>`).join("")}</tbody></table></div></div>`;
}
function CalDayLabel(ymd) { const [y, m, d] = ymd.split("-"); return `${d}-${m}-${y.slice(2)}`; }

/* ---- lead groups ---- */
async function loadBuckets() {
  const b = await api("/api/buckets"); if (b.error) return;
  const s = await api("/api/stats");
  $("#bkStats").innerHTML = [["Total leads", b.total, 1], ["Free to assign", b.unassigned, 1],
    ...b.buckets.filter(x => x.total).slice(0, 4).map(x => [x.label, x.total, 0])]
    .map(([l, n, hi]) => `<div class="stat ${hi ? "hi" : ""}"><div class="n">${(n || 0).toLocaleString()}</div><div class="l">${esc(l)}</div></div>`).join("");
  const ag = b.agents;
  $("#bkTbl").innerHTML = "<thead><tr><th>Age group</th><th class='r'>Total</th><th class='r'>Free</th>" +
    ag.map(a => `<th class='r'>${esc(a)}</th>`).join("") + "<th></th></tr></thead><tbody>" +
    b.buckets.map(x => `<tr data-act="bucket" data-key="${esc(x.key)}"><td class="co"><b>${esc(x.label)}</b></td>
      <td class="r"><b>${x.total.toLocaleString()}</b></td>
      <td class="r">${x.unassigned ? `<span class="pill s-Interested">${x.unassigned.toLocaleString()}</span>` : '<span style="color:var(--ink-4)">0</span>'}</td>
      ${ag.map(a => `<td class="r">${x.by_agent[a] ? x.by_agent[a].toLocaleString() : '<span style="color:var(--ink-4)">—</span>'}</td>`).join("")}
      <td class="r"><button class="btn sec sm" data-act="bucket" data-key="${esc(x.key)}">Use</button></td></tr>`).join("") +
    `<tr style="background:var(--surface-2)"><td><b>TOTAL</b></td><td class="r"><b>${b.total.toLocaleString()}</b></td><td class="r"><b>${b.unassigned.toLocaleString()}</b></td>
      ${ag.map(a => `<td class="r"><b>${b.buckets.reduce((t, x) => t + (x.by_agent[a] || 0), 0).toLocaleString()}</b></td>`).join("")}<td></td></tr></tbody>`;
  $("#bkPick").innerHTML = b.buckets.map(x => `<option value="${esc(x.key)}">${esc(x.label)} — ${x.unassigned.toLocaleString()} free</option>`).join("");
  if (s.agents) $("#bkAgent").innerHTML = s.agents.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join("");
  if (BK_SEL) $("#bkPick").value = BK_SEL;
  bucketDetail();
}
function pickBucket(k) { BK_SEL = k; $("#bkPick").value = k; bucketDetail(); }
async function bucketDetail() {
  const k = $("#bkPick").value; if (!k) return; BK_SEL = k;
  const d = await api("/api/bucket/" + encodeURIComponent(k)); if (d.error) return;
  const pc = n => d.total ? Math.round(n / d.total * 100) : 0;
  $("#bkDetail").innerHTML = `
    <div class="kv mb"><dt>In group</dt><dd><b>${(d.total || 0).toLocaleString()}</b></dd>
      <dt>Free</dt><dd><b>${(d.unassigned || 0).toLocaleString()}</b></dd>
      <dt>Has phone</dt><dd>${(d.with_phone || 0).toLocaleString()} (${pc(d.with_phone || 0)}%)</dd>
      <dt>Has email</dt><dd>${(d.with_email || 0).toLocaleString()} (${pc(d.with_email || 0)}%)</dd>
      <dt>Has cell</dt><dd>${(d.with_cell || 0).toLocaleString()} (${pc(d.with_cell || 0)}%)</dd>
      <dt>Insured</dt><dd>${(d.insured || 0).toLocaleString()} (${pc(d.insured || 0)}%)</dd></div>
    ${(d.formats || []).length ? `<div class="tbl-wrap mb"><table><thead><tr><th>MC number type</th><th class="r">In group</th><th class="r">Free</th></tr></thead>
      <tbody>${d.formats.map(f => `<tr><td><span class="badge">${esc(f.fmt || "—")}</span></td><td class="r">${(f.n || 0).toLocaleString()}</td><td class="r"><b>${(f.free || 0).toLocaleString()}</b></td></tr>`).join("")}</tbody></table></div>` : ""}
    ${(d.states || []).length ? `<div class="tbl-wrap"><table><thead><tr><th>Top states</th><th class="r">Leads</th></tr></thead>
      <tbody>${d.states.map(x => `<tr><td>${esc(x.state || "—")}</td><td class="r"><b>${x.n.toLocaleString()}</b></td></tr>`).join("")}</tbody></table></div>` : ""}`;
}
async function assignBucket() {
  const fmt = $("#bkFormat").value;
  const body = { agent_id: +$("#bkAgent").value, count: +$("#bkCount").value, bucket: $("#bkPick").value, state: $("#bkState").value.trim().toUpperCase(), format: fmt };
  const r = await api("/api/assign", { method: "POST", body: JSON.stringify(body) });
  $("#bkMsg").textContent = r.error ? ("Error: " + r.error)
    : `Assigned ${r.assigned} ${fmt ? fmt + " " : ""}leads from this group to ${r.agent}.` + (r.assigned < +$("#bkCount").value ? " (that is all that was available)" : "");
  if (!r.error) toast(`${r.assigned} leads assigned to ${r.agent}`);
  loadBuckets();
}

/* ---- dashboard ---- */
async function loadStats() {
  const s = await api("/api/stats"); if (s.error) return;
  $("#stats").innerHTML = [["Total leads", s.total, 1], ["Unassigned", s.unassigned, 0], ["New", (s.by_status.New || 0), 0],
    ["Contacted", (s.by_status.Contacted || 0), 0], ["Interested", (s.by_status.Interested || 0), 1], ["Closed", (s.by_status.Closed || 0), 0]]
    .map(([l, n, hi]) => `<div class="stat ${hi ? "hi" : ""}"><div class="n">${(n || 0).toLocaleString()}</div><div class="l">${esc(l)}</div></div>`).join("");
  $("#agentTbl").innerHTML = "<thead><tr><th>Agent</th><th class='r'>Assigned</th><th class='r'>Untouched</th><th class='r'>Interested</th><th class='r'>Closed</th><th class='r'>Calls</th></tr></thead><tbody>" +
    (s.agents.length ? s.agents.map(a => `<tr data-act="report" data-uid="${a.id}"><td class="co"><b>${esc(a.name)}</b></td><td class="r">${a.assigned || 0}</td>
      <td class="r">${a.untouched || 0}</td><td class="r">${a.interested || 0}</td><td class="r">${a.closed || 0}</td><td class="r">${a.calls || 0}</td></tr>`).join("")
      : `<tr><td colspan="6" class="empty">No agents yet.</td></tr>`) + "</tbody>";
  $("#asgAgent").innerHTML = s.agents.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join("");
  loadMatrix();
}
async function loadMatrix() {
  const m = await api("/api/matrix"); if (m.error) return;
  const head = "<thead><tr><th>Owner</th>" + m.statuses.map(s => `<th class='r'>${esc(s)}</th>`).join("") + "<th class='r'>Total</th></tr></thead>";
  const agentIds = {}; (BOOT.users || []).forEach(u => agentIds[u.name] = u.id);
  const row = r => {
    const id = agentIds[r.name];
    return `<tr ${id ? `data-act="report" data-uid="${id}"` : ""}><td class="co"><b>${esc(r.name)}</b></td>` +
      m.statuses.map(s => `<td class="r">${r[s] ? `<span class="pill ${ST_CLASS[s] || "s-New"}">${r[s].toLocaleString()}</span>` : '<span style="color:var(--ink-4)">0</span>'}</td>`).join("") +
      `<td class="r"><b>${(r.total || 0).toLocaleString()}</b></td></tr>`;
  };
  $("#matrixTbl").innerHTML = head + "<tbody>" + m.rows.map(row).join("") +
    `<tr style="background:var(--surface-2)">` + row(m.totals).replace(/^<tr[^>]*>/, "").replace("</tr>", "") + `</tr></tbody>`;
}
async function openReport(id) {
  const r = await api("/api/agent/" + id); if (r.error) { toast(r.error, false); return; }
  $("#repAv").textContent = ini(r.user.name); $("#repName").textContent = r.user.name;
  $("#repRole").textContent = r.user.role + " · " + r.assigned.toLocaleString() + " leads assigned";
  const pct = r.assigned ? Math.round(r.touched / r.assigned * 100) : 0;
  const st = Object.entries(r.by_status).sort((a, b) => b[1] - a[1]);
  const dp = Object.entries(r.by_disposition).filter(([k]) => k !== "(none)");
  $("#repBody").innerHTML = `
    <div class="stats" style="margin-bottom:14px">
      <div class="stat hi"><div class="n">${r.assigned.toLocaleString()}</div><div class="l">Assigned</div></div>
      <div class="stat"><div class="n">${r.touched.toLocaleString()}</div><div class="l">Worked (${pct}%)</div></div>
      <div class="stat"><div class="n">${r.calls.toLocaleString()}</div><div class="l">Call attempts</div></div>
      <div class="stat"><div class="n">${r.followups.toLocaleString()}</div><div class="l">Follow-ups set</div></div></div>
    <div class="card mb"><h3>Status breakdown</h3><div class="tbl-wrap"><table><tbody>${st.length ? st.map(([k, v]) =>
      `<tr><td>${pill(k)}</td><td class="r"><b>${v.toLocaleString()}</b></td></tr>`).join("") : '<tr><td class="empty">Nothing assigned yet.</td></tr>'}</tbody></table></div></div>
    ${dp.length ? `<div class="card mb"><h3>Call outcomes</h3><div class="tbl-wrap"><table><tbody>${dp.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="r"><b>${v}</b></td></tr>`).join("")}</tbody></table></div></div>` : ""}
    ${r.due.length ? `<div class="card mb"><h3>Upcoming follow-ups</h3><div class="tbl-wrap"><table><tbody>${r.due.map(d => `<tr><td class="co"><b>${esc(d.legal_name)}</b><span class="s">${esc(d.phone || "")}</span></td><td class="r">${esc(d.next_followup)}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
    <div class="card"><h3>Recent activity</h3><div class="tbl-wrap"><table><tbody>${r.recent.length ? r.recent.map(a =>
      `<tr><td class="co"><b>${esc(a.legal_name || "—")}</b><span class="s">${esc((a.detail || "").slice(0, 90))}</span></td><td class="r" style="color:var(--ink-4);font-size:0.7188rem">${esc(a.ts)}</td></tr>`).join("") : '<tr><td class="empty">No activity recorded yet.</td></tr>'}</tbody></table></div></div>`;
  $("#repModal").classList.remove("hide");
}
async function doAssign() {
  const fmt = $("#asgFormat").value;
  const b = { agent_id: +$("#asgAgent").value, count: +$("#asgCount").value, fresh: $("#asgFresh").checked, state: $("#asgState").value.trim().toUpperCase(), format: fmt };
  const r = await api("/api/assign", { method: "POST", body: JSON.stringify(b) });
  $("#asgMsg").textContent = r.error ? ("Error: " + r.error)
    : `Assigned ${r.assigned} ${fmt ? fmt + " " : ""}leads to ${r.agent}.` + (r.assigned < +$("#asgCount").value ? " (that is all that was available)" : "");
  if (!r.error) toast(`${r.assigned} leads assigned`);
  loadStats();
}
async function doSync() {
  const r = await api("/api/sync", { method: "POST", body: JSON.stringify({ days: +$("#syncDays").value, format: $("#syncFormat").value }) });
  if (r.error) { toast(r.error, false); return; }
  $("#btnSync").disabled = true; pollSync();
}
async function pollSync() {
  clearTimeout(SYNCT);
  const s = await api("/api/sync/status"); if (s.error) return;
  $("#syncMsg").textContent = s.msg || ""; $("#syncBar").style.width = (s.pct || 0) + "%"; $("#btnSync").disabled = !!s.running;
  if (s.running) SYNCT = setTimeout(pollSync, 1800);
  else if (s.added) { toast(`${s.added} new leads added`); loadStats(); }
}

/* ---- team ---- */
async function loadTeam() {
  const s = await api("/api/stats"); if (s.error) return;
  $("#teamTbl").innerHTML = "<thead><tr><th>Name</th><th>Role</th><th class='r'>Assigned</th><th>PIN</th><th></th></tr></thead><tbody>" +
    (BOOT.users || []).map(u => {
      const a = s.agents.find(x => x.id === u.id);
      return `<tr><td class="co"><b>${esc(u.name)}</b></td><td><span class="tag">${esc(u.role)}</span></td>
        <td class="r">${a ? a.assigned.toLocaleString() : "—"}</td><td style="color:var(--ink-4);letter-spacing:3px">••••</td>
        <td class="r">${a ? `<button class="btn sec sm" data-act="report" data-uid="${u.id}">Report</button> ` : ""}
          <button class="btn sec sm" data-act="reset" data-uid="${u.id}" data-name="${esc(u.name)}">Reset PIN</button></td></tr>`;
    }).join("") + "</tbody>";
}
function openReset(id, nm) { RSTU = id; $("#rstName").textContent = nm; $("#rstPin").value = ""; $("#rstErr").textContent = ""; $("#rstModal").classList.remove("hide"); setTimeout(() => $("#rstPin").focus(), 60); }
async function saveReset() {
  const p = $("#rstPin").value.trim();
  if (!/^\d{4,12}$/.test(p)) { $("#rstErr").textContent = "PIN must be 4–12 digits."; return; }
  const r = await api("/api/resetpin", { method: "POST", body: JSON.stringify({ user_id: RSTU, pin: p }) });
  if (r.error) { $("#rstErr").textContent = r.error; return; }
  $("#rstModal").classList.add("hide"); toast(`${r.name}'s PIN is set — tell them directly`); loadTeam();
}
async function addUser() {
  const b = { name: $("#nuName").value.trim(), role: $("#nuRole").value, pin: $("#nuPin").value.trim() };
  const r = await api("/api/user", { method: "POST", body: JSON.stringify(b) });
  if (r.error) { $("#nuMsg").textContent = "Error: " + r.error; return; }
  $("#nuMsg").textContent = `${b.name} created.`; $("#nuName").value = ""; $("#nuPin").value = "";
  BOOT = await api("/api/bootstrap"); loadTeam(); toast("Account created");
}

/* ---- leads ---- */
async function load() {
  const q = $("#q").value.trim();
  const day = (FUMODE || q) ? "" : ($("#fDate").value || "");
  const p = new URLSearchParams({ scope: $("#fScope").value, status: $("#fStatus").value, bucket: $("#fBucket").value,
    format: $("#fFormat").value, q: q, day: day, followup: FUMODE ? "due" : "", fresh: $("#fFresh").checked ? "1" : "", limit: 300 });
  const d = await api("/api/leads?" + p); if (d.error) return;
  ROWS = d.rows; TOTAL = d.total; DAYSHOWN = day; renderRows();
  $("#searchHint").classList.toggle("hide", !(q && $("#fDate").value && !FUMODE));
}
function renderRows() {
  $("#count").textContent = `${ROWS.length} of ${TOTAL.toLocaleString()}` + (DAYSHOWN ? ` · assigned ${dayLabel(DAYSHOWN)}` : "");
  $("#tbl").innerHTML = "<thead><tr><th>Company</th><th>State</th><th class='r'>Trucks</th><th class='r'>Age</th><th>Status</th>" +
    (ME.role === "admin" ? "<th>Agent</th>" : "") + "</tr></thead><tbody>" +
    (ROWS.length ? ROWS.map((r, i) => `<tr data-act="sel" data-idx="${i}" id="r${i}">
      <td class="co"><b>${esc(r.legal_name)}</b><span class="s">${esc(r.city)} · ${esc(r.docket)}</span></td>
      <td>${esc(r.state)}</td><td class="r">${esc(r.power_units)}</td>
      <td class="r">${r.age == null ? "—" : (r.age <= 7 ? '<span class="freshdot"></span>' : "") + r.age + "d"}</td>
      <td>${pill(r.status)}</td>${ME.role === "admin" ? `<td>${esc(r.agent || "—")}</td>` : ""}</tr>`).join("")
      : `<tr><td colspan="6" class="empty">${emptyMsg(DAYSHOWN)}</td></tr>`) + "</tbody>";
}
function matchesFilters(l) {
  if (FUMODE) { const f = (l.next_followup || "").trim(); if (!f || f > localToday()) return false; }
  const st = $("#fStatus").value; if (st && (l.status || "") !== st) return false;
  const bk = $("#fBucket").value; if (bk && (l.bucket || "") !== bk) return false;
  const fm = $("#fFormat").value; if (fm && String(l.number_format || "").toUpperCase() !== fm) return false;
  return true;
}
function sel(i) {
  document.querySelectorAll("#tbl tr.sel").forEach(t => t.classList.remove("sel"));
  const tr = $("#r" + i); if (tr) tr.classList.add("sel");
  CUR = ROWS[i]; const r = CUR, p = e164(r.phone), c = e164(r.cell);
  $("#detailHead").innerHTML = `<h2>${esc(r.legal_name)}</h2><div class="sub">${esc(r.officer || "No officer on file")} · ${esc(r.city)}, ${esc(r.state)}</div>`;
  const bkLabel = r.bucket ? ((BOOT.buckets.find(b => b.key === r.bucket) || {}).label || r.bucket) : "";
  $("#detailIn").innerHTML = `
    <div class="dial">
      ${p ? `<a href="rcapp://r/call?number=${esc(p)}"><svg><use href="#ic-phone"/></svg>${esc(r.phone)}</a>` : ""}
      ${c ? `<a class="c2" href="rcapp://r/call?number=${esc(c)}"><svg><use href="#ic-mobile"/></svg>${esc(r.cell)}</a>` : ""}</div>
    ${localTimeBox(r.state)}
    ${r.email ? `<a class="mailto" target="_blank" rel="noopener" href="https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(r.email)}"><svg class="i sm"><use href="#ic-mail"/></svg>${esc(r.email)}</a>` : ""}
    <dl class="kv">
      <dt>USDOT</dt><dd>${esc(r.usdot)}</dd>
      <dt>Docket</dt><dd>${esc(r.docket)} <span class="badge motus">${esc(r.number_format)}</span></dd>
      <dt>Authority</dt><dd>${esc(r.authority_date)} · ${r.age == null ? "?" : r.age} days ago${bkLabel ? ` <span class="badge">${esc(bkLabel)}</span>` : ""}</dd>
      <dt>Fleet</dt><dd>${esc(r.power_units)} trucks · ${esc(r.drivers)} drivers</dd>
      <dt>Insurance</dt><dd>${esc(r.insurance_company || "None on file")}</dd>
      <dt>Address</dt><dd>${esc(r.street)}, ${esc(r.city)} ${esc(r.zip)}</dd>
      <dt>Updated</dt><dd>${esc(r.updated_at || "never")}${r.updated_by ? " · " + esc(r.updated_by) : ""}</dd></dl>
    <div class="frm">
      <label class="field full"><span class="flabel">Status</span><select class="inp" id="dStatus">${(BOOT.statuses || []).map(s => `<option ${s === r.status ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
      <label class="field full"><span class="flabel">Disposition</span><select class="inp" id="dDisp">${(BOOT.dispositions || []).map(s => `<option ${s === r.disposition ? "selected" : ""}>${esc(s || "—")}</option>`).join("")}</select></label>
      <label class="field"><span class="flabel">Last contact</span><input class="inp" type="date" id="dLast" value="${esc(r.last_contact || "")}"/></label>
      <label class="field"><span class="flabel">Follow-up</span><input class="inp" type="date" id="dNext" value="${esc(r.next_followup || "")}"/></label>
      <label class="field full"><span class="flabel">Call attempts</span><input class="inp" type="number" id="dAtt" min="0" value="${r.call_attempts || 0}"/></label>
      <label class="field full"><span class="flabel">Notes (summary)</span><textarea class="inp" id="dNotes" placeholder="Key facts worth keeping at the top">${esc(r.notes || "")}</textarea></label></div>
    <button class="btn block" id="saveBtn" data-act="save"><svg><use href="#ic-check"/></svg>Save changes</button>
    <div style="border-top:1px solid var(--hair);padding-top:12px">
      <div class="section-label">Call history</div>
      <div class="frm" style="margin-bottom:8px">
        <label class="field full"><span class="flabel">Outcome</span><select class="inp" id="lgOutcome">${(BOOT.dispositions || []).map(s => `<option value="${esc(s)}">${esc(s || "—")}</option>`).join("")}</select></label>
        <label class="field full"><span class="flabel">What happened</span><textarea class="inp" id="lgNote" placeholder="e.g. spoke to owner, hauling reefer out of Fresno, call back Monday"></textarea></label></div>
      <button class="btn sec block" id="logBtn" data-act="logcall">Log this call</button>
      <div class="logbox" id="logList" style="margin-top:10px"><div class="logempty">Loading…</div></div></div>`;
  loadLog(r.id);
}
async function loadLog(leadId) {
  const d = await api("/api/calllog/" + leadId); const box = $("#logList"); if (!box) return;
  if (d.error) { box.innerHTML = `<div class="logempty">${esc(d.error)}</div>`; return; }
  box.innerHTML = d.rows.length ? d.rows.map(e => `<div class="logitem"><div class="meta"><b>${esc(e.who)}</b>
    ${e.outcome ? `<span class="badge">${esc(e.outcome)}</span>` : ""}<span style="margin-left:auto">${esc(e.ts)}</span></div>
    ${e.note ? `<div class="txt">${esc(e.note)}</div>` : ""}</div>`).join("")
    : `<div class="logempty">No calls logged yet. Every entry you add is kept — nothing overwrites.</div>`;
}
async function logCall() {
  if (!CUR) return;
  const btn = $("#logBtn"), outcome = $("#lgOutcome").value, note = $("#lgNote").value.trim();
  if (!outcome && !note) { toast("Pick an outcome or write a note first", false); return; }
  btn.disabled = true; btn.textContent = "Saving…";
  const r = await api("/api/calllog/" + CUR.id, { method: "POST", body: JSON.stringify({ outcome, note, bump: true }) });
  btn.disabled = false; btn.textContent = "Log this call";
  if (r.error) { toast("Error: " + r.error, false); return; }
  $("#lgNote").value = "";
  if (r.lead) { Object.assign(CUR, r.lead); const att = $("#dAtt"); if (att) att.value = CUR.call_attempts || 0;
    const i = ROWS.findIndex(x => x.id === CUR.id); if (i >= 0) ROWS[i] = Object.assign(ROWS[i], r.lead); }
  loadLog(CUR.id); toast("Call logged");
}
async function save() {
  if (!CUR) return;
  const btn = $("#saveBtn"), was = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
  const d = $("#dDisp").value;
  const r = await api("/api/lead/" + CUR.id, { method: "POST", body: JSON.stringify({
    status: $("#dStatus").value, disposition: d === "—" ? "" : d, last_contact: $("#dLast").value,
    next_followup: $("#dNext").value, call_attempts: +$("#dAtt").value, notes: $("#dNotes").value }) });
  if (r.error) { if (btn) { btn.disabled = false; btn.textContent = was; } toast("Error: " + r.error, false); return; }
  Object.assign(CUR, r.lead);
  const i = ROWS.findIndex(x => x.id === CUR.id);
  if (i >= 0 && !matchesFilters(r.lead)) {
    ROWS.splice(i, 1); TOTAL = Math.max(0, TOTAL - 1); renderRows();
    if (ROWS.length) { sel(Math.min(i, ROWS.length - 1)); }
    else { CUR = null; $("#detailHead").innerHTML = `<h2 style="font-size:0.875rem;color:var(--ink-3)">Lead details</h2>`;
      $("#detailIn").innerHTML = `<div class="empty"><div class="ic"><svg><use href="#ic-check"/></svg></div>All done for this filter.</div>`; }
    toast(FUMODE ? "Saved — callback cleared" : `Saved — moved out of "${$("#fStatus").value}"`); refreshFollowupBadge(); return;
  }
  refreshFollowupBadge();
  if (i >= 0) { ROWS[i] = Object.assign(ROWS[i], r.lead);
    const tr = $("#r" + i); if (tr) { const cell = tr.querySelector("td:nth-child(5)"); if (cell) cell.innerHTML = pill(CUR.status); } }
  if (btn) { btn.disabled = false; btn.textContent = "Saved ✓"; setTimeout(() => { if (btn) btn.innerHTML = '<svg><use href="#ic-check"/></svg>Save changes'; }, 1400); }
  toast("Saved");
}

/* ---- date helpers ---- */
function localToday() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function dayLabel(ymd) { const [y, m, d] = ymd.split("-"); return `${d}-${m}-${y.slice(2)}`; }
function dateToday() { $("#fDate").value = localToday(); load(); }
function dateShift(n) {
  const cur = $("#fDate").value || localToday(); const d = new Date(cur + "T12:00:00"); d.setDate(d.getDate() + n);
  $("#fDate").value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; load();
}
function emptyMsg(day) {
  if (FUMODE) return `Nothing is due. Every callback you promised is done.`;
  if (!day) return "No leads match these filters.";
  const isToday = day === localToday();
  return `Nothing was assigned to you on <b>${dayLabel(day)}</b>${isToday ? " yet" : ""}.`;
}
function clearFilters() {
  $("#q").value = ""; $("#fStatus").value = ""; $("#fFresh").checked = false; $("#fBucket").value = ""; $("#fFormat").value = "";
  if (!$("#fScope").disabled) $("#fScope").value = ME.role === "admin" ? "all" : "mine";
  if (ME.role !== "admin" && !FUMODE) $("#fDate").value = localToday();
  load();
}

/* ---- change pin ---- */
function openPin() { ["cpOld", "cpNew", "cpNew2"].forEach(i => $("#" + i).value = ""); $("#cpErr").textContent = ""; $("#pinModal").classList.remove("hide"); setTimeout(() => $("#cpOld").focus(), 60); }
async function savePin() {
  const o = $("#cpOld").value.trim(), n = $("#cpNew").value.trim(), n2 = $("#cpNew2").value.trim();
  const min = ME.role === "admin" ? 6 : 4;
  if (!new RegExp(`^\\d{${min},12}$`).test(n)) { $("#cpErr").textContent = `New PIN must be ${min}–12 digits.`; return; }
  if (n !== n2) { $("#cpErr").textContent = "New PINs do not match."; return; }
  const r = await api("/api/changepin", { method: "POST", body: JSON.stringify({ old: o, new: n }) });
  if (r.error) { $("#cpErr").textContent = r.error; return; }
  $("#pinModal").classList.add("hide"); toast("PIN changed");
}

/* ===================== wiring (no inline handlers) ===================== */
function on(id, ev, fn) { const el = $(id); if (el) el.addEventListener(ev, fn); }
on("#btnSignin", "click", doLogin);
on("#btnBack", "click", backToUsers);
on("#pinBox", "keydown", e => { if (e.key === "Enter") doLogin(); });
on("#btnLogout", "click", logout);
on("#btnPin", "click", openPin);
on("#btnTheme", "click", toggleTheme);
on("#btnText", "click", cycleText);
on("#btnRefresh", "click", refresh);
on("#btnAssign", "click", doAssign);
on("#btnSync", "click", doSync);
on("#btnAssignBucket", "click", assignBucket);
on("#btnAddUser", "click", addUser);
on("#btnClear", "click", clearFilters);
on("#btnDatePrev", "click", () => dateShift(-1));
on("#btnDateNext", "click", () => dateShift(1));
on("#btnDateToday", "click", dateToday);
on("#btnSavePin", "click", savePin);
on("#btnCancelPin", "click", () => $("#pinModal").classList.add("hide"));
on("#btnSaveReset", "click", saveReset);
on("#btnCancelReset", "click", () => $("#rstModal").classList.add("hide"));
on("#btnCloseReport", "click", () => $("#repModal").classList.add("hide"));
on("#bkPick", "change", bucketDetail);
on("#cmdk", "click", () => { if (ME) { go("leads"); setTimeout(() => $("#q").focus(), 30); } });
["fStatus", "fScope", "fFresh", "fBucket", "fDate", "fFormat"].forEach(id => on("#" + id, "change", load));
on("#dvAgent", "change", e => { DV.agent = e.target.value; openMonths(); });
let _t; on("#q", "input", () => { clearTimeout(_t); _t = setTimeout(load, 350); });
on("#q", "keydown", e => { if (e.key === "Enter") { clearTimeout(_t); load(); } });
$("#navWrap").addEventListener("click", e => { const a = e.target.closest("a[data-view]"); if (a) go(a.dataset.view); });
document.addEventListener("keydown", e => {
  if (e.key === "/" && ME && VIEW === "leads" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
    e.preventDefault(); $("#q").focus();
  }
});
/* delegated actions for generated content */
document.addEventListener("click", e => {
  const el = e.target.closest("[data-act]"); if (!el) return;
  const a = el.dataset.act, D = el.dataset;
  if (a === "pick") pick(+D.uid);
  else if (a === "sel") sel(+D.idx);
  else if (a === "report") openReport(+D.uid);
  else if (a === "reset") openReset(+D.uid, D.name);
  else if (a === "bucket") pickBucket(D.key);
  else if (a === "save") save();
  else if (a === "logcall") logCall();
  else if (a === "months") openMonths();
  else if (a === "days") openDays(D.arg || D.month);
  else if (a === "day") openDay(D.arg || D.day);
  else if (a === "dayleads") openDayLeads(D.day, +D.agent);
});
