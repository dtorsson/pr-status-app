"use strict";

// ---------- bridge ----------
function post(msg) {
  window.webkit?.messageHandlers?.app?.postMessage(msg);
}

let state = null;

// ---------- dom helpers ----------
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
const $ = (id) => document.getElementById(id);

// ---------- rendering ----------
const CI_CLASS = { success: "ok", failure: "fail", pending: "pend" };
const REVIEW_PILLS = {
  APPROVED: { cls: "pill-ok", icon: "✓", label: "approved" },
  CHANGES_REQUESTED: { cls: "pill-fail", icon: "✕", label: "changes requested" },
  COMMENTED: { cls: "pill-neutral", icon: "\u{1F4AC}", label: "commented" },
  PENDING: { cls: "pill-neutral", icon: "⌛", label: "pending" },
};

function prRow(pr, { showAuthor = false } = {}) {
  const row = el("div", "pr-row");
  row.appendChild(el("span", `ci-dot ${CI_CLASS[pr.ci_status] || "none"}`));
  const main = el("div", "pr-main");
  const line = el("div", "pr-line");
  line.appendChild(el("span", "pr-ref mono", `${pr.repository?.name ?? "?"} #${pr.number}`));
  line.appendChild(el("span", "pr-title", pr.title || ""));
  main.appendChild(line);
  if (showAuthor) main.appendChild(el("div", "pr-author", `@${pr.author?.login ?? "?"}`));
  row.appendChild(main);
  row.addEventListener("click", () => post({ action: "openPR", url: pr.url }));
  return row;
}

function sectionCard(icon, title, count, badgeCls) {
  const card = el("section", "card");
  const head = el("div", "card-header");
  head.appendChild(el("span", "card-icon", icon));
  head.appendChild(el("span", "card-title", title));
  head.appendChild(el("span", `count-badge ${badgeCls}`, String(count)));
  card.appendChild(head);
  const body = el("div", "card-body");
  card.appendChild(body);
  return { card, body };
}

function render(s) {
  tickClock();
  state = s;
  // Never yank the user out of settings on a background poll; only switch
  // views when settings is not currently open. Settings fields are untouched.
  if ($("view-settings").hidden) {
    $("view-main").hidden = false;
  }
  document.querySelector(".online-dot").classList.toggle("offline", !s.ok);
  const cards = $("cards");
  cards.replaceChildren();

  if (!s.ok) {
    renderError(s, cards);
    updateFooter(s);
    reportHeight();
    return;
  }

  const counts = s.counts ?? {};
  const reviewPRs = s.review ?? [];
  const minePRs = s.mine ?? [];
  const teamPRs = s.team ?? [];

  const review = sectionCard("\u{1F50D}", "Waiting for your review",
    counts.review ?? 0, (counts.review ?? 0) > 0 ? "badge-warn" : "badge-ok");
  if (!reviewPRs.length) review.body.appendChild(el("div", "empty", "All clear ✨"));
  for (const pr of reviewPRs) review.body.appendChild(prRow(pr, { showAuthor: true }));
  cards.appendChild(review.card);

  const mine = sectionCard("\u{1F6E0}", "Your open PRs", counts.mine ?? 0, "badge-neutral");
  if (!minePRs.length) mine.body.appendChild(el("div", "empty", "No open PRs"));
  for (const pr of minePRs) {
    mine.body.appendChild(prRow(pr));
    for (const r of pr.reviews ?? []) {
      const p = REVIEW_PILLS[r.state] ??
        { cls: "pill-neutral", icon: "○", label: (r.state || "review").toLowerCase().replace(/_/g, " ") };
      const pill = el("div", `review-pill ${p.cls}`);
      pill.appendChild(el("span", null, `${p.icon} ${p.label}`));
      pill.appendChild(el("span", "pill-author", `· @${r.author ?? "?"}`));
      mine.body.appendChild(pill);
    }
  }
  cards.appendChild(mine.card);

  if (teamPRs.length) {
    const team = sectionCard("\u{1F465}", "Team", counts.team ?? 0, "badge-neutral");
    let cur = null;
    for (const pr of teamPRs) {
      const author = pr.author?.login ?? "?";
      if (author !== cur) {
        team.body.appendChild(el("div", "team-author mono", `@${author}`));
        cur = author;
      }
      team.body.appendChild(prRow(pr));
    }
    cards.appendChild(team.card);
  }

  updateFooter(s);
  reportHeight();
}
window.render = render;

function renderError(s, cards) {
  const card = el("section", "card error");
  const head = el("div", "card-header");
  head.appendChild(el("span", "card-icon", "⚠️"));
  head.appendChild(el("span", "card-title", "Something went wrong"));
  card.appendChild(head);
  const body = el("div", "card-body");
  body.appendChild(el("div", "error-summary", s.error_summary || "Unknown error"));
  const detail = (s.error_detail || "").trim();
  if (detail) {
    body.appendChild(el("div", "error-detail",
      detail.split("\n").slice(0, 5).join("\n")));
  }
  const retry = el("button", "retry-btn", "Retry");
  retry.addEventListener("click", () => post({ action: "refresh" }));
  body.appendChild(retry);
  card.appendChild(body);
  cards.appendChild(card);
}

function updateFooter(s) {
  $("meta").textContent =
    `last ${s.last_refresh ?? "—"} · every ${s.poll_interval_label ?? "?"}`;
  setRefreshing(Boolean(s.refreshing));
}

function setRefreshing(on) {
  $("refresh").classList.toggle("spinning", on);
}
window.setRefreshing = setRefreshing;

function reportHeight() {
  requestAnimationFrame(() => {
    // `main` is flex:1 inside a 100vh column, so it stretches to fill the
    // viewport — its scrollHeight tracks the current popover height and would
    // only ever grow. Measure intrinsic content height instead: the children
    // plus main's own vertical padding.
    const main = document.querySelector("main:not([hidden])");
    const style = getComputedStyle(main);
    let content = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    for (const child of main.children) {
      const cs = getComputedStyle(child);
      if (cs.display === "none") continue;
      content += child.offsetHeight +
        parseFloat(cs.marginTop) + parseFloat(cs.marginBottom);
    }
    const h = document.querySelector("header").offsetHeight +
      content +
      document.querySelector("footer").offsetHeight;
    post({ action: "height", value: Math.ceil(h) });
  });
}

// ---------- clock ----------
function tickClock() {
  const d = new Date();
  $("clock").textContent =
    `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// ---------- settings ----------
const PRESETS = [[60, "1m"], [300, "5m"], [600, "10m"], [1800, "30m"]];
let pendingInterval = 300;

function parseInterval(text) {
  text = text.trim().toLowerCase();
  if (!text) throw new Error("empty input");
  if (/^\d+$/.test(text)) return parseInt(text, 10);
  const UNITS = { h: 3600, m: 60, s: 1 };
  let total = 0, num = "";
  for (const ch of text) {
    if (/\d/.test(ch)) num += ch;
    else if (ch in UNITS) {
      if (!num) throw new Error("missing number before unit");
      total += UNITS[ch] * parseInt(num, 10);
      num = "";
    } else if (/\s/.test(ch)) continue;
    else throw new Error(`unknown character: '${ch}'`);
  }
  if (num) throw new Error("number without unit");
  if (total <= 0) throw new Error("interval must be positive");
  return total;
}

function renderPills() {
  const wrap = $("interval-pills");
  wrap.replaceChildren();
  for (const [secs, label] of PRESETS) {
    const b = el("button", "pill" + (secs === pendingInterval ? " active" : ""), label);
    b.addEventListener("click", () => {
      pendingInterval = secs;
      $("interval-custom").value = "";
      renderPills();
    });
    wrap.appendChild(b);
  }
}

function openSettings() {
  $("view-main").hidden = true;
  $("view-settings").hidden = false;
  pendingInterval = state?.settings?.poll_interval_seconds ?? 300;
  renderPills();
  $("teammates").value = (state?.settings?.teammates ?? []).join("\n");
  $("interval-custom").value = "";
  $("settings-error").hidden = true;
  $("signin").textContent =
    state?.ok ? `Signed in as @${state.me} · ${state.orgs}` : "";
  reportHeight();
}

function closeSettings() {
  $("view-settings").hidden = true;
  $("view-main").hidden = false;
  reportHeight();
}

function showSettingsError(msg) {
  const banner = $("settings-error");
  banner.textContent = msg;
  banner.hidden = false;
  reportHeight();
}
// Native rejected a save after we optimistically closed settings. Bring the
// view back without repopulating fields — the user's typed values are still
// in the DOM and must be preserved.
window.settingsError = (msg) => {
  $("view-main").hidden = true;
  $("view-settings").hidden = false;
  showSettingsError(msg);
  reportHeight();
};

function saveSettings() {
  let secs = pendingInterval;
  const custom = $("interval-custom").value.trim();
  if (custom) {
    try { secs = parseInterval(custom); }
    catch (e) { return showSettingsError(e.message); }
  }
  if (secs < 10) return showSettingsError("Minimum 10s to avoid GitHub rate limits.");
  const teammates = $("teammates").value
    .split(/[\n,]/).map((t) => t.trim()).filter(Boolean);
  post({ action: "saveSettings", poll_interval_seconds: secs, teammates });
  closeSettings();
}

// ---------- demo data (browser-only styling check) ----------
const DEMO_STATE = {
  ok: true,
  review: [
    { repository: { name: "frontend" }, number: 4242, title: "feat(ci): add PR diff-coverage report", author: { login: "adamsco" }, url: "#", ci_status: "success" },
    { repository: { name: "kognic-ui" }, number: 791, title: "Add fixedSize column option to List", author: { login: "HampusRonstromWork" }, url: "#", ci_status: "pending" },
  ],
  mine: [
    { repository: { name: "annotell" }, number: 11200, title: "feat(workflow): add preventNonGeometryEdits review setting", url: "#", ci_status: "success",
      reviews: [{ author: "olinoskar", state: "APPROVED" }] },
    { repository: { name: "frontend" }, number: 4215, title: "feat(backend-notifications): handle correction-task-available notification", url: "#", ci_status: "failure",
      reviews: [{ author: "gustaf", state: "CHANGES_REQUESTED" }] },
  ],
  team: [
    { repository: { name: "annotell" }, number: 11210, title: "Estimated-input-quality-based review selection", author: { login: "olinoskar" }, url: "#", ci_status: "failure" },
    { repository: { name: "kognic-cd" }, number: 2111, title: "Deploy annotell-db-migration to staging-eu", author: { login: "gustaf-annotell" }, url: "#", ci_status: "none" },
  ],
  counts: { review: 2, mine: 2, team: 2 },
  me: "dtorsson", orgs: "annotell,kognic-internal",
  last_refresh: "12:36:40", poll_interval_label: "5m", refreshing: false,
  settings: { poll_interval_seconds: 300, teammates: ["olinoskar", "gustaf-annotell"] },
};

// ---------- init ----------
document.addEventListener("DOMContentLoaded", () => {
  tickClock();
  setInterval(tickClock, 30_000);
  $("gear").addEventListener("click", () =>
    $("view-settings").hidden ? openSettings() : closeSettings());
  $("refresh").addEventListener("click", () => {
    setRefreshing(true);
    post({ action: "refresh" });
  });
  $("quit").addEventListener("click", () => post({ action: "quit" }));
  $("settings-back").addEventListener("click", closeSettings);
  $("settings-save").addEventListener("click", saveSettings);

  if (new URLSearchParams(location.search).has("demo")) {
    render(DEMO_STATE);
  }
  post({ action: "ready" });
});
