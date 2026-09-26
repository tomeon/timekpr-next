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
  group: null,
  server: null,
};

/* The resources the limits form can be bound to.  Each target owns one
   instance of the form (`form`), the configuration it last showed
   (`config`) and the hours being edited (`hours`). */
const targets = {
  user: {
    prefix: "",
    hasHideTrayIcon: true,
    hasOverrides: false,
    configPath: () => `/users/${encode(state.user)}/config`,
    reload: () => loadUser(),
    // the daemon creates the user's policy on the first save
    afterSave: async () => {
      await loadUser();
      await loadUsers();
    },
  },
  group: {
    prefix: "group-",
    hasHideTrayIcon: false,
    hasOverrides: true,
    configPath: () => `/groups/${encode(state.group)}/config`,
    reload: () => loadGroup(),
    afterSave: () => loadGroups(),
  },
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

/* a semicolon separated list, as the CLI writes them */
function splitList(text) {
  return text
    .split(";")
    .map((s) => s.trim())
    .filter(Boolean);
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
  return splitList(text).map((item) => {
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

function encode(name) {
  return encodeURIComponent(name);
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

/* the list entry for a user or a group: a name, a tag and a second line */
function listItem(name, selected, tag, sub, onSelect) {
  const li = document.createElement("li");
  li.classList.toggle("selected", selected);
  li.innerHTML = `<div class="name"></div><span class="tag"></span><div class="sub"></div>`;
  $(".name", li).textContent = name;
  $(".tag", li).textContent = tag;
  $(".sub", li).textContent = sub;
  li.addEventListener("click", onSelect);
  return li;
}

function markSelected(listId, name) {
  for (const li of document.querySelectorAll(`#${listId} li`))
    li.classList.toggle("selected", $(".name", li).textContent === name);
}

async function deletePolicy(path, reload) {
  if (!confirm("Delete this policy?")) return;
  try {
    await api("DELETE", path);
    await reload();
    message("Policy deleted", true);
  } catch (err) {
    message(err.message);
  }
}

/* ---- pages ---- */

function showPage(name) {
  for (const page of document.querySelectorAll(".page"))
    page.hidden = page.id !== `page-${name}`;
  for (const button of document.querySelectorAll("nav button"))
    button.classList.toggle("active", button.id === `nav-${name}`);
  if (name === "groups") loadGroups().catch((err) => message(err.message));
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

/* the user list's policy_source: "user", "group:<g1>;<g2>", "default" or
   "unresolved" (the user's groups could not be looked up) */
function policyTag(source) {
  if (source === "user") return "own policy";
  if (source.startsWith("group:")) return `groups: ${source.slice(6)}`;
  if (source === "unresolved") return "unresolved";
  return "defaults";
}

async function loadUsers() {
  const users = await api("GET", "/users?include=status");
  const list = $("#user-list");
  list.replaceChildren(
    ...users.map((user) => {
      const li = listItem(
        user.username,
        user.username === state.user,
        policyTag(user.policy_source),
        `${user.full_name ? `${user.full_name} · ` : ""}${hms(user.status.time_left_day)} left today`,
        () => selectUser(user.username),
      );
      $(".name", li).prepend(
        Object.assign(document.createElement("span"), {
          className: `dot ${user.status.session_active ? "active" : ""}`,
        }),
      );
      return li;
    }),
  );
}

async function selectUser(username) {
  state.user = username;
  markSelected("user-list", username);
  $("#user-title").textContent = username;
  $("#user-detail").hidden = false;
  await loadUser();
}

async function loadUser() {
  const user = await api("GET", `/users/${encode(state.user)}`);
  // a user without a policy of their own gets one, holding only the
  // settings that are changed, the first time a setting is saved
  const created =
    " (a change creates the user's own policy, holding only what is changed)";
  const groups = user.policy_groups.join(", ");
  $("#user-policy").textContent =
    user.policy_source === "user"
      ? groups
        ? `Policy: own settings, the rest from groups ${groups}`
        : "Policy: own"
      : user.policy_source === "group"
        ? `Policy: from groups ${groups}${created}`
        : `Policy: defaults${created}`;
  $("#delete-policy").disabled = user.policy_source !== "user";
  renderStatus(user.status);
  renderConfig(targets.user, user.config);
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

/* ---- groups ---- */

async function loadGroups() {
  const groups = await api("GET", "/groups");
  $("#groups-list").replaceChildren(
    ...groups.map((group) =>
      listItem(
        group.group,
        group.group === state.group,
        group.overrides.length ? `overrides: ${group.overrides.join(";")}` : "",
        `members: ${group.members.join(", ") || "none"}`,
        () => selectGroup(group.group),
      ),
    ),
  );
}

async function selectGroup(group) {
  state.group = group;
  markSelected("groups-list", group);
  $("#group-title").textContent = group;
  $("#group-detail").hidden = false;
  await loadGroup();
}

async function loadGroup() {
  const group = await api("GET", `/groups/${encode(state.group)}`);
  renderConfig(targets.group, group.config);
}

async function addGroup(event) {
  event.preventDefault();
  const input = $("#group-name");
  const group = input.value.trim();
  if (!group) return message("Enter a group name");
  try {
    // an empty change still creates the group's policy
    await api("PATCH", `/groups/${encode(group)}/config`, {});
    input.value = "";
    message("Policy created", true);
    await loadGroups();
    await selectGroup(group);
  } catch (err) {
    message(err.message);
  }
}

async function deleteGroupPolicy() {
  await deletePolicy(`/groups/${encode(state.group)}/policy`, async () => {
    state.group = null;
    $("#group-detail").hidden = true;
    await loadGroups();
  });
}

/* ---- the limits form, for a user or a group ---- */

/* instantiate the template into `form` and bind it to `target` */
function bindConfigForm(target, form) {
  form.append($("#config-template").content.cloneNode(true));
  target.form = form;
  $("table.days", form).id = `${target.prefix}day-limits`;
  $("table.hours", form).id = `${target.prefix}hours-grid`;
  if (!target.hasHideTrayIcon)
    $("[name=hide_tray_icon]", form).closest("label").remove();
  if (!target.hasOverrides)
    $("[name=overrides]", form).closest(".card").remove();
  form.addEventListener("submit", (event) => saveConfig(target, event));
  $("[name=reload]", form).addEventListener("click", () =>
    target.reload().catch((err) => message(err.message)),
  );
}

function renderDayTable(form, allowedDays, limits) {
  $("table.days tbody", form).replaceChildren(
    ...DAYS.map((day) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${DAY_NAMES[day - 1]}</td><td><input type="checkbox" name="allowed_${day}"></td><td><input type="text" size="6" name="limit_${day}"></td>`;
      $(`[name=allowed_${day}]`, tr).checked = allowedDays.includes(day);
      $(`[name=limit_${day}]`, tr).value = hms(limits[day] ?? 0);
      return tr;
    }),
  );
}

function readDayTable(form) {
  const allowed_days = DAYS.filter((day) => form[`allowed_${day}`].checked);
  const limits_per_day = {};
  for (const day of allowed_days)
    limits_per_day[day] = parseHms(form[`limit_${day}`].value);
  return { allowed_days, limits_per_day };
}

function renderHoursGrid(target) {
  $("table.hours tbody", target.form).replaceChildren(
    ...DAYS.map((day) => {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.textContent = DAY_NAMES[day - 1];
      tr.append(th);
      const byHour = new Map(target.hours[day].map((e) => [e.hour, e]));
      for (const hour of HOURS) {
        const td = document.createElement("td");
        const entry = byHour.get(hour);
        td.textContent = hour;
        td.title = entry ? hoursToText([entry]) : `${hour}: not allowed`;
        if (entry) td.classList.add(entry.unaccounted ? "uacc" : "on");
        if (entry && (entry.start_minute !== 0 || entry.end_minute !== 60))
          td.classList.add("partial");
        td.addEventListener("click", () => cycleHour(target, day, hour));
        tr.append(td);
      }
      const td = document.createElement("td");
      const input = document.createElement("input");
      input.type = "text";
      input.value = hoursToText(target.hours[day]);
      input.addEventListener("change", () => {
        try {
          target.hours[day] = textToHours(input.value);
          renderHoursGrid(target);
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

function cycleHour(target, day, hour) {
  const entries = target.hours[day].filter((e) => e.hour !== hour);
  const current = target.hours[day].find((e) => e.hour === hour);
  if (!current)
    entries.push({ hour, start_minute: 0, end_minute: 60, unaccounted: false });
  else if (!current.unaccounted)
    entries.push({ hour, start_minute: 0, end_minute: 60, unaccounted: true });
  target.hours[day] = entries.sort((a, b) => a.hour - b.hour);
  renderHoursGrid(target);
}

function renderConfig(target, config) {
  target.config = config;
  target.hours = Object.fromEntries(
    DAYS.map((day) => [day, config.allowed_hours[day].map((e) => ({ ...e }))]),
  );
  const form = target.form;
  renderDayTable(form, config.allowed_days, config.limits_per_day);
  renderHoursGrid(target);
  form.limit_per_week.value = hms(config.limit_per_week);
  form.limit_per_month.value = hms(config.limit_per_month);
  form.track_inactive.checked = config.track_inactive;
  if (target.hasHideTrayIcon)
    form.hide_tray_icon.checked = config.hide_tray_icon;
  if (target.hasOverrides) form.overrides.value = config.overrides.join(";");
}

function readConfig(target) {
  const form = target.form;
  const config = {
    ...readDayTable(form),
    allowed_hours: target.hours,
    limit_per_week: parseHms(form.limit_per_week.value),
    limit_per_month: parseHms(form.limit_per_month.value),
    track_inactive: form.track_inactive.checked,
  };
  if (target.hasHideTrayIcon)
    config.hide_tray_icon = form.hide_tray_icon.checked;
  if (target.hasOverrides) config.overrides = splitList(form.overrides.value);
  return config;
}

async function saveConfig(target, event) {
  event.preventDefault();
  try {
    const next = readConfig(target);
    const patch = diff(target.config, next);
    // only the changed days of the hours, and nothing when none changed
    if (patch.allowed_hours) {
      patch.allowed_hours = diff(
        target.config.allowed_hours,
        next.allowed_hours,
      );
      if (Object.keys(patch.allowed_hours).length === 0)
        delete patch.allowed_hours;
    }
    if (Object.keys(patch).length === 0)
      return message("Nothing changed", true);
    renderConfig(target, await api("PATCH", target.configPath(), patch));
    message("Saved", true);
    await target.afterSave();
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
          ? splitList(input.value)
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

/* delete the user policies left over from versions that created one per
   user (they restrict nothing but hide the group policies) */
async function migratePolicies(dryRun) {
  if (!dryRun && !confirm("Delete every user policy that restricts nothing?"))
    return;
  try {
    const { users } = await api("POST", "/policies/migrate", {
      dry_run: dryRun,
    });
    $("#migrate-result").textContent =
      users.length === 0
        ? "No user policy restricts nothing; nothing to delete"
        : `${dryRun ? "Would delete" : "Deleted"} the policies of: ${users.join(", ")}`;
    if (!dryRun) await loadUsers();
  } catch (err) {
    message(err.message);
  }
}

/* ---- wiring ---- */

for (const name of ["users", "groups", "server", "token"])
  $(`#nav-${name}`).addEventListener("click", () => showPage(name));
bindConfigForm(targets.user, $("#config-form"));
bindConfigForm(targets.group, $("#group-config-form"));
$("#delete-policy").addEventListener("click", () =>
  deletePolicy(`/users/${encode(state.user)}/policy`, targets.user.afterSave),
);
$("#time-left-form").addEventListener("submit", applyTimeLeft);
$("#group-add-form").addEventListener("submit", addGroup);
$("#delete-group-policy").addEventListener("click", deleteGroupPolicy);
$("#server-form").addEventListener("submit", saveServer);
$("#server-reload").addEventListener("click", loadServer);
$("#migrate-dry-run").addEventListener("click", () => migratePolicies(true));
$("#migrate-delete").addEventListener("click", () => migratePolicies(false));
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
