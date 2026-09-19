/* timekpr web UI: a thin client for the API in docs/web-api.md */
"use strict";

const API = "api/v1";
const DAYS = [1, 2, 3, 4, 5, 6, 7];
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOURS = Array.from({ length: 24 }, (_, hour) => hour);
const SERVER_FIELDS = [
  ["log_level", "Log level (1–3)", "number"],
  ["poll_time", "Poll interval", "number"],
  ["save_time", "Save interval", "number"],
  ["termination_time", "Countdown before termination", "number"],
  ["final_warning_time", "Final warning", "number"],
  ["final_notification_time", "Final notification", "number"],
  ["session_types_tracked", "Session types tracked", "list"],
  ["session_types_excluded", "Session types excluded", "list"],
  ["users_excluded", "Users excluded", "list"],
];

const $ = (selector, root = document) => root.querySelector(selector);
const state = {
  token: sessionStorage.getItem("timekprw-token") || "",
  user: null,
  config: null,
  hours: {},
  server: null,
};

/* ---- helpers ---- */

function message(text, ok = false) {
  const el = $("#message");
  el.textContent = text;
  el.classList.toggle("ok", ok);
  el.hidden = !text;
}

function hms(seconds) {
  if (seconds === null || seconds === undefined) return "–";
  const sign = seconds < 0 ? "-" : "";
  seconds = Math.abs(seconds);
  const h = Math.floor(seconds / 3600),
    m = Math.floor((seconds % 3600) / 60);
  return `${sign}${h}:${String(m).padStart(2, "0")}`;
}

function parseHms(text) {
  const match = /^\s*(\d+)(?::([0-5]?\d))?\s*$/.exec(text);
  if (!match) throw new Error(`"${text}" is not h:mm`);
  return Number(match[1]) * 3600 + Number(match[2] || 0) * 60;
}

/* the CLI's notation for a day's hours: 7;8;11[0-30];!14 */
function hoursToText(entries) {
  return entries
    .map(
      (e) =>
        `${e.unaccounted ? "!" : ""}${e.hour}${e.start_minute === 0 && e.end_minute === 60 ? "" : `[${e.start_minute}-${e.end_minute}]`}`,
    )
    .join(";");
}

function textToHours(text) {
  return text
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((item) => {
      const match = /^(!?)(\d{1,2})(?:\[(\d{1,2})-(\d{1,2})\])?$/.exec(item);
      if (!match) throw new Error(`"${item}" is not an hour specification`);
      return {
        hour: Number(match[2]),
        start_minute: Number(match[3] ?? 0),
        end_minute: Number(match[4] ?? 60),
        unaccounted: match[1] === "!",
      };
    });
}

async function api(method, path, body) {
  const headers = { Accept: "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    if (response.status === 401) showPage("token");
    const errors = (data.errors || [])
      .map((e) => `${e.field}: ${e.message}`)
      .join("; ");
    throw new Error(
      `${data.title}${data.detail ? `: ${data.detail}` : ""}${errors ? ` (${errors})` : ""}`,
    );
  }
  return data;
}

function encode(username) {
  return encodeURIComponent(username);
}

/* JSON with object keys sorted, so that key order does not count as a change */
function canonical(value) {
  return JSON.stringify(value, (_key, val) =>
    val && typeof val === "object" && !Array.isArray(val)
      ? Object.fromEntries(
          Object.keys(val)
            .sort()
            .map((k) => [k, val[k]]),
        )
      : val,
  );
}

/* which top-level fields of `next` differ from `previous` */
function diff(previous, next) {
  const changes = {};
  for (const key of Object.keys(next)) {
    if (canonical(previous[key]) !== canonical(next[key]))
      changes[key] = next[key];
  }
  return changes;
}

/* ---- pages ---- */

function showPage(name) {
  for (const page of document.querySelectorAll(".page"))
    page.hidden = page.id !== `page-${name}`;
  for (const button of document.querySelectorAll("nav button"))
    button.classList.toggle("active", button.id === `nav-${name}`);
  if (name === "server") loadServer();
}

async function refreshHealth() {
  const el = $("#health");
  try {
    const response = await fetch(`${API}/health`);
    const health = await response.json();
    el.textContent =
      health.daemon === "ok"
        ? `daemon ${health.timekpr_version}`
        : "daemon unreachable";
    el.className = `badge ${health.daemon === "ok" ? "ok" : "bad"}`;
  } catch (err) {
    el.textContent = "no connection";
    el.className = "badge bad";
  }
}

/* ---- users ---- */

async function loadUsers() {
  const users = await api("GET", "/users?include=status");
  const list = $("#user-list");
  list.replaceChildren(
    ...users.map((user) => {
      const li = document.createElement("li");
      li.classList.toggle("selected", user.username === state.user);
      li.innerHTML = `<div class="name"><span class="dot ${user.status.session_active ? "active" : ""}"></span></div><div class="sub"></div>`;
      $(".name", li).append(user.username);
      $(".sub", li).textContent =
        `${user.full_name ? `${user.full_name} · ` : ""}${hms(user.status.time_left_day)} left today`;
      li.addEventListener("click", () => selectUser(user.username));
      return li;
    }),
  );
}

async function selectUser(username) {
  state.user = username;
  for (const li of document.querySelectorAll("#user-list li"))
    li.classList.toggle("selected", $(".name", li).textContent === username);
  $("#user-title").textContent = username;
  $("#user-detail").hidden = false;
  await loadUser();
}

async function loadUser() {
  const user = await api("GET", `/users/${encode(state.user)}`);
  renderStatus(user.status);
  renderConfig(user.config);
}

function renderStatus(status) {
  const rows = [
    ["Time left today", hms(status.time_left_day)],
    ["Left in a row", hms(status.time_left_continuous)],
    ["Spent today", hms(status.time_spent_day)],
    ["Spent this week", hms(status.time_spent_week)],
    ["Spent this month", hms(status.time_spent_month)],
    ["Session", status.session_active ? "active" : "none"],
  ];
  $("#user-status").replaceChildren(
    ...rows.flatMap(([label, value]) => {
      const dt = document.createElement("dt"),
        dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = value;
      return [dt, dd];
    }),
  );
}

function renderDayTable(tableId, prefix, allowedDays, limits) {
  $(`#${tableId} tbody`).replaceChildren(
    ...DAYS.map((day) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${DAY_NAMES[day - 1]}</td><td><input type="checkbox" name="${prefix}allowed_${day}"></td><td><input type="text" size="6" name="${prefix}limit_${day}"></td>`;
      $(`[name=${prefix}allowed_${day}]`, tr).checked =
        allowedDays.includes(day);
      $(`[name=${prefix}limit_${day}]`, tr).value = hms(limits[day] ?? 0);
      return tr;
    }),
  );
}

function readDayTable(form, prefix) {
  const allowed_days = DAYS.filter(
    (day) => form[`${prefix}allowed_${day}`].checked,
  );
  const limits_per_day = {};
  for (const day of allowed_days)
    limits_per_day[day] = parseHms(form[`${prefix}limit_${day}`].value);
  return { allowed_days, limits_per_day };
}

function renderHoursGrid() {
  $("#hours-grid tbody").replaceChildren(
    ...DAYS.map((day) => {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.textContent = DAY_NAMES[day - 1];
      tr.append(th);
      const byHour = new Map(state.hours[day].map((e) => [e.hour, e]));
      for (const hour of HOURS) {
        const td = document.createElement("td");
        const entry = byHour.get(hour);
        td.textContent = hour;
        td.title = entry ? hoursToText([entry]) : `${hour}: not allowed`;
        if (entry) td.classList.add(entry.unaccounted ? "uacc" : "on");
        if (entry && (entry.start_minute !== 0 || entry.end_minute !== 60))
          td.classList.add("partial");
        td.addEventListener("click", () => cycleHour(day, hour));
        tr.append(td);
      }
      const td = document.createElement("td");
      const input = document.createElement("input");
      input.type = "text";
      input.value = hoursToText(state.hours[day]);
      input.addEventListener("change", () => {
        try {
          state.hours[day] = textToHours(input.value);
          renderHoursGrid();
        } catch (err) {
          message(err.message);
        }
      });
      td.append(input);
      tr.append(td);
      return tr;
    }),
  );
}

function cycleHour(day, hour) {
  const entries = state.hours[day].filter((e) => e.hour !== hour);
  const current = state.hours[day].find((e) => e.hour === hour);
  if (!current)
    entries.push({ hour, start_minute: 0, end_minute: 60, unaccounted: false });
  else if (!current.unaccounted)
    entries.push({ hour, start_minute: 0, end_minute: 60, unaccounted: true });
  state.hours[day] = entries.sort((a, b) => a.hour - b.hour);
  renderHoursGrid();
}

function renderConfig(config) {
  state.config = config;
  state.hours = Object.fromEntries(
    DAYS.map((day) => [day, config.allowed_hours[day].map((e) => ({ ...e }))]),
  );
  const form = $("#config-form");
  renderDayTable("day-limits", "", config.allowed_days, config.limits_per_day);
  renderHoursGrid();
  form.limit_per_week.value = hms(config.limit_per_week);
  form.limit_per_month.value = hms(config.limit_per_month);
  form.track_inactive.checked = config.track_inactive;
  form.hide_tray_icon.checked = config.hide_tray_icon;
}

function readConfig() {
  const form = $("#config-form");
  return {
    ...readDayTable(form, ""),
    allowed_hours: state.hours,
    limit_per_week: parseHms(form.limit_per_week.value),
    limit_per_month: parseHms(form.limit_per_month.value),
    track_inactive: form.track_inactive.checked,
    hide_tray_icon: form.hide_tray_icon.checked,
  };
}

async function saveConfig(event) {
  event.preventDefault();
  try {
    const next = readConfig();
    const patch = diff(state.config, next);
    // only the changed days of the hours, and nothing when none changed
    if (patch.allowed_hours) {
      patch.allowed_hours = diff(
        state.config.allowed_hours,
        next.allowed_hours,
      );
      if (Object.keys(patch.allowed_hours).length === 0)
        delete patch.allowed_hours;
    }
    if (Object.keys(patch).length === 0)
      return message("Nothing changed", true);
    renderConfig(
      await api("PATCH", `/users/${encode(state.user)}/config`, patch),
    );
    message("Saved", true);
    await loadUsers();
  } catch (err) {
    message(err.message);
  }
}

async function applyTimeLeft(event) {
  event.preventDefault();
  const form = event.target;
  try {
    const body = {
      operation: form.operation.value,
      seconds: parseHms(form.amount.value),
    };
    renderStatus(
      await api("POST", `/users/${encode(state.user)}/time-left`, body),
    );
    message("Applied", true);
    await loadUsers();
  } catch (err) {
    message(err.message);
  }
}

/* ---- daemon settings ---- */

async function loadServer() {
  try {
    state.server = await api("GET", "/config");
  } catch (err) {
    return message(err.message);
  }
  $("#server-fields").replaceChildren(
    ...SERVER_FIELDS.map(([field, label, kind]) => {
      const tr = document.createElement("tr");
      const th = document.createElement("th"),
        td = document.createElement("td");
      th.textContent = label;
      const input = document.createElement("input");
      input.name = field;
      input.type =
        kind === "checkbox"
          ? "checkbox"
          : kind === "number"
            ? "number"
            : "text";
      if (kind === "checkbox") input.checked = state.server[field];
      else if (kind === "list") input.value = state.server[field].join(";");
      else input.value = state.server[field];
      if (kind === "list") input.size = 40;
      td.append(input);
      tr.append(th, td);
      return tr;
    }),
  );
}

async function saveServer(event) {
  event.preventDefault();
  const form = event.target;
  const next = {};
  for (const [field, , kind] of SERVER_FIELDS) {
    const input = form[field];
    next[field] =
      kind === "checkbox"
        ? input.checked
        : kind === "list"
          ? input.value
              .split(";")
              .map((s) => s.trim())
              .filter(Boolean)
          : Number(input.value);
  }
  const patch = diff(state.server, next);
  if (Object.keys(patch).length === 0) return message("Nothing changed", true);
  try {
    state.server = await api("PATCH", "/config", patch);
    message("Saved", true);
    await loadServer();
  } catch (err) {
    message(err.message);
  }
}

/* ---- wiring ---- */

for (const name of ["users", "server", "token"])
  $(`#nav-${name}`).addEventListener("click", () => showPage(name));
$("#config-form").addEventListener("submit", saveConfig);
$("#config-reload").addEventListener("click", () =>
  loadUser().catch((err) => message(err.message)),
);
$("#time-left-form").addEventListener("submit", applyTimeLeft);
$("#server-form").addEventListener("submit", saveServer);
$("#server-reload").addEventListener("click", loadServer);
$("#token-form").addEventListener("submit", (event) => {
  event.preventDefault();
  state.token = event.target.token.value.trim();
  sessionStorage.setItem("timekprw-token", state.token);
  showPage("users");
  loadUsers().then(
    () => message(""),
    (err) => message(err.message),
  );
});

refreshHealth();
setInterval(refreshHealth, 30000);
loadUsers().catch((err) => message(err.message));
setInterval(() => {
  if (!$("#page-users").hidden) loadUsers().catch(() => {});
}, 15000);
