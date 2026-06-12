# Popover UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rumps `NSMenu` dropdown with a Claude God-style dark popover (cards, pills, hover states, in-popover settings) rendered in a `WKWebView`.

**Architecture:** Pure PyObjC shell (`NSStatusItem` + `NSPopover` + `WKWebView`) loads static `ui/` web content. Python pushes state via `evaluateJavaScript("window.render(...)")`; JS posts actions back through `WKScriptMessageHandler`. Pure logic lives in a new `core.py` (tested with pytest). Data source unchanged: `pr-status --json` subprocess, now run on a background thread.

**Tech Stack:** Python 3.10+, PyObjC (Cocoa + WebKit), vanilla HTML/CSS/JS, py2app, pytest.

**Spec:** `docs/superpowers/specs/2026-06-12-popover-ui-design.md`

**Environment:** All commands assume `source .venv/bin/activate` in the repo root.

---

## File structure

| File | Responsibility |
|---|---|
| `core.py` (new) | Pure logic: constants, interval parse/format, config IO, `script_path`/`ui_path`/`subprocess_env`, `build_state`, `error_state`, `compute_notifications`, `update_seen`, `pr_key`. No AppKit imports. |
| `tests/test_core.py` (new) | pytest coverage for `core.py`. |
| `ui/index.html`, `ui/style.css`, `ui/app.js` (new) | All rendering: main view, settings view, error state, JS↔native bridge calls. Browser-testable via `?demo=1`. |
| `app.py` (rewritten in Task 3) | PyObjC shell: status item, popover, webview, timer, background fetch, notifications, bridge dispatch. |
| `setup.py`, `requirements.txt`, `README.md` | Packaging + docs updates. |

---

### Task 1: Extract pure logic into `core.py` with tests

Old `app.py` stays untouched and working (rumps) until Task 3. `core.py` starts as a copy of the pure helpers plus new state/notification functions.

**Files:**
- Create: `core.py`
- Create: `tests/test_core.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_core.py
import core


def _pr(repo="acme/frontend", num=1, title="T", author="alice", reviews=None, ci="success"):
    return {
        "repository": {"name": repo.split("/")[1], "nameWithOwner": repo},
        "number": num,
        "title": title,
        "author": {"login": author},
        "url": f"https://github.com/{repo}/pull/{num}",
        "reviews": reviews if reviews is not None else [],
        "ci_status": ci,
    }


def _data(review=(), mine=(), team=()):
    return {
        "review": list(review),
        "mine": list(mine),
        "team": list(team),
        "counts": {"review": len(review), "mine": len(mine), "team": len(team)},
        "me": "dtorsson",
        "orgs": "annotell,kognic-internal",
    }


# --- interval helpers (moved verbatim from app.py) ---

def test_parse_interval_formats():
    assert core.parse_interval("90s") == 90
    assert core.parse_interval("3m") == 180
    assert core.parse_interval("1h30m") == 5400
    assert core.parse_interval("45") == 45


def test_parse_interval_rejects_garbage():
    import pytest
    for bad in ("", "m5", "5x", "5m3"):
        with pytest.raises(ValueError):
            core.parse_interval(bad)


def test_format_interval():
    assert core.format_interval(300) == "5m"
    assert core.format_interval(5400) == "1h 30m"
    assert core.format_interval(45) == "45s"


# --- state building ---

def test_build_state_shape():
    data = _data(review=[_pr()], mine=[_pr(num=2)])
    state = core.build_state(
        data, last_refresh="12:36:40", poll_interval=300, teammates=["bob"]
    )
    assert state["ok"] is True
    assert state["counts"] == {"review": 1, "mine": 1, "team": 0}
    assert state["me"] == "dtorsson"
    assert state["last_refresh"] == "12:36:40"
    assert state["poll_interval_label"] == "5m"
    assert state["refreshing"] is False
    assert state["settings"] == {
        "poll_interval_seconds": 300,
        "teammates": ["bob"],
    }


def test_error_state_shape():
    state = core.error_state("Script timeout", "line1\nline2")
    assert state["ok"] is False
    assert state["error_summary"] == "Script timeout"
    assert state["error_detail"] == "line1\nline2"


# --- notification diffing ---

def test_new_review_request_notifies():
    pr = _pr(num=7, title="Add thing", author="adamsco")
    notes = core.compute_notifications(_data(review=[pr]), set(), {})
    assert len(notes) == 1
    assert notes[0]["title"] == "New review request"
    assert notes[0]["subtitle"] == "frontend #7"
    assert "adamsco" in notes[0]["message"]
    assert notes[0]["url"] == pr["url"]


def test_seen_review_request_does_not_notify():
    pr = _pr(num=7)
    seen = {core.pr_key(pr)}
    assert core.compute_notifications(_data(review=[pr]), seen, {}) == []


def test_reviewer_state_change_notifies():
    pr = _pr(num=3, reviews=[{"author": "olinoskar", "state": "APPROVED"}])
    prev = {core.pr_key(pr): {"olinoskar": "COMMENTED"}}
    notes = core.compute_notifications(_data(mine=[pr]), set(), prev)
    assert len(notes) == 1
    assert notes[0]["title"] == "@olinoskar approved"


def test_unchanged_reviewer_state_does_not_notify():
    pr = _pr(num=3, reviews=[{"author": "olinoskar", "state": "APPROVED"}])
    prev = {core.pr_key(pr): {"olinoskar": "APPROVED"}}
    assert core.compute_notifications(_data(mine=[pr]), set(), prev) == []


def test_pending_state_does_not_notify():
    pr = _pr(num=3, reviews=[{"author": "olinoskar", "state": "PENDING"}])
    assert core.compute_notifications(_data(mine=[pr]), set(), {}) == []


def test_update_seen():
    r = _pr(num=1)
    m = _pr(num=2, reviews=[{"author": "bob", "state": "APPROVED"}])
    seen_review, seen_mine = core.update_seen(_data(review=[r], mine=[m]))
    assert seen_review == {("acme/frontend", 1)}
    assert seen_mine == {("acme/frontend", 2): {"bob": "APPROVED"}}


# --- config IO ---

def test_save_and_load_teammates_roundtrip(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("orgs=acme\nmax_age_days=21\n[teammates]\nold\n", encoding="utf-8")
    core.save_teammates(["alice", "bob"], path=cfg)
    parsed = core.load_pr_config(cfg)
    assert parsed["teammates"] == ["alice", "bob"]
    assert "orgs=acme" in parsed["top"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pip install pytest && python3 -m pytest tests/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core'`

- [ ] **Step 3: Create `core.py`**

Move these from `app.py` **verbatim** (do not edit their bodies): `DEFAULT_POLL_INTERVAL`, `SCRIPT_TIMEOUT_SECONDS`, `PRESET_INTERVALS`, `CONFIG_DIR`, `PR_CONFIG_PATH`, `APP_CONFIG_PATH`, `TEAMMATES_HEADER`, `REVIEW_STATE_LABELS`, `format_interval`, `parse_interval`, `load_app_config`, `save_app_config`, `load_pr_config`, `save_teammates`, `subprocess_env`, `script_path`. Leave `app.py` unchanged (duplication is temporary, deleted in Task 3). Then add the new functions:

```python
# core.py — append after the moved helpers

def ui_path() -> Path:
    """Locate ui/ dir in dev (next to core.py) or bundled (Resources/ui)."""
    here = Path(__file__).resolve().parent
    candidates = [here / "ui", here.parent / "Resources" / "ui"]
    for c in candidates:
        if c.exists():
            return c
    return here / "ui"


def pr_key(pr: dict) -> tuple[str, int]:
    return pr["repository"]["nameWithOwner"], int(pr["number"])


def build_state(
    data: dict,
    *,
    last_refresh: str | None,
    poll_interval: int,
    teammates: list[str],
    refreshing: bool = False,
) -> dict:
    """Shape the script's JSON plus app metadata for window.render()."""
    return {
        "ok": True,
        "review": data.get("review", []),
        "mine": data.get("mine", []),
        "team": data.get("team", []),
        "counts": data.get("counts", {"review": 0, "mine": 0, "team": 0}),
        "me": data.get("me", "?"),
        "orgs": data.get("orgs", ""),
        "last_refresh": last_refresh,
        "poll_interval_label": format_interval(poll_interval),
        "refreshing": refreshing,
        "settings": {
            "poll_interval_seconds": poll_interval,
            "teammates": teammates,
        },
    }


def error_state(summary: str, detail: str) -> dict:
    return {"ok": False, "error_summary": summary, "error_detail": detail}


def compute_notifications(
    data: dict,
    seen_review: set[tuple[str, int]],
    seen_mine_reviews: dict[tuple[str, int], dict[str, str]],
) -> list[dict]:
    """Diff against previously seen state; return notifications to deliver."""
    notes: list[dict] = []
    for pr in data.get("review", []):
        if pr_key(pr) not in seen_review:
            notes.append({
                "title": "New review request",
                "subtitle": f"{pr['repository']['name']} #{pr['number']}",
                "message": f"{pr['title']} — by @{pr.get('author', {}).get('login', '?')}",
                "url": pr.get("url", ""),
            })
    for pr in data.get("mine", []):
        current = {
            r.get("author", "?"): r.get("state", "")
            for r in (pr.get("reviews") or [])
        }
        previous = seen_mine_reviews.get(pr_key(pr), {})
        for author, state in current.items():
            if previous.get(author) != state and state in REVIEW_STATE_LABELS:
                notes.append({
                    "title": f"@{author} {REVIEW_STATE_LABELS[state]}",
                    "subtitle": f"{pr['repository']['name']} #{pr['number']}",
                    "message": pr.get("title", ""),
                    "url": pr.get("url", ""),
                })
    return notes


def update_seen(data: dict) -> tuple[set, dict]:
    seen_review = {pr_key(pr) for pr in data.get("review", [])}
    seen_mine = {
        pr_key(pr): {
            r.get("author", "?"): r.get("state", "")
            for r in (pr.get("reviews") or [])
        }
        for pr in data.get("mine", [])
    }
    return seen_review, seen_mine
```

Module header for `core.py`:

```python
"""Pure logic for the PR Status app: config IO, intervals, state building,
notification diffing. No AppKit imports — fully unit-testable."""
from __future__ import annotations

import json
import os
from pathlib import Path
```

(Note: `REVIEW_STATE_DISPLAY` and `CI_DOTS` from old `app.py` are NOT moved — rendering moves to JS. They die with the rewrite in Task 3.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "refactor: extract pure logic into core.py with tests"
```

---

### Task 2: Static UI — `ui/index.html`, `ui/style.css`, `ui/app.js`

Browser-testable without the native shell: `?demo=1` renders sample data; bridge `post()` is a no-op outside WKWebView.

**Files:**
- Create: `ui/index.html`
- Create: `ui/style.css`
- Create: `ui/app.js`

- [ ] **Step 1: Create `ui/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="style.css">
</head>
<body>
<div id="root">
  <header>
    <div class="glyph">P</div>
    <div class="title">PR Status<span class="online-dot"></span></div>
    <div class="spacer"></div>
    <div id="clock" class="clock mono"></div>
    <button id="gear" class="icon-btn" title="Settings">&#9881;</button>
  </header>

  <main id="view-main">
    <div id="cards"><div class="empty">Loading…</div></div>
  </main>

  <main id="view-settings" hidden>
    <section class="card">
      <div class="card-header"><span class="card-title">Refresh every</span></div>
      <div class="card-body">
        <div class="pills" id="interval-pills"></div>
        <input id="interval-custom" class="text-input mono"
               placeholder="custom: 90s, 3m, 1h30m" spellcheck="false">
      </div>
    </section>
    <section class="card">
      <div class="card-header"><span class="card-title">Teammates</span></div>
      <div class="card-body">
        <textarea id="teammates" class="text-input" rows="6"
                  placeholder="one username per line" spellcheck="false"></textarea>
        <div class="hint">Empty disables the Team section.</div>
      </div>
    </section>
    <div id="settings-error" class="error-banner" hidden></div>
    <div class="settings-actions">
      <button id="settings-back" class="footer-btn">&#8249; Back</button>
      <button id="settings-save" class="footer-btn primary">Save</button>
    </div>
    <div class="signin" id="signin"></div>
  </main>

  <footer>
    <button id="refresh" class="footer-btn"><span id="refresh-icon">&#10227;</span> Refresh</button>
    <div id="meta" class="meta mono"></div>
    <button id="quit" class="footer-btn">Quit</button>
  </footer>
</div>
<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `ui/style.css`**

```css
:root {
  --bg: #1d1d1f;
  --card: #2a2a2c;
  --card-border: rgba(255, 255, 255, 0.08);
  --text: #e8e8ea;
  --text-dim: #98989d;
  --green: #30d158;
  --red: #ff453a;
  --yellow: #ffd60a;
  --gray: #636366;
  --purple: #7d56f4;
  --hover: rgba(255, 255, 255, 0.06);
  --mono: ui-monospace, "SF Mono", Menlo, monospace;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

html, body { background: var(--bg); }

body {
  font: 13px/1.45 -apple-system, BlinkMacSystemFont, sans-serif;
  color: var(--text);
  -webkit-user-select: none;
  user-select: none;
  cursor: default;
  overflow: hidden;
}

.mono { font-family: var(--mono); }

#root { display: flex; flex-direction: column; height: 100vh; }

/* ---------- header ---------- */
header {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 14px 10px;
}
.glyph {
  width: 26px; height: 26px; border-radius: 7px;
  background: var(--purple); color: #fff;
  display: grid; place-items: center;
  font-weight: 700; font-size: 14px;
}
.title { font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 6px; }
.online-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green); display: inline-block;
}
.spacer { flex: 1; }
.clock { color: var(--text-dim); font-size: 12px; }
.icon-btn {
  background: none; border: none; color: var(--text-dim);
  font-size: 15px; cursor: pointer; padding: 4px 6px; border-radius: 6px;
}
.icon-btn:hover { background: var(--hover); color: var(--text); }

/* ---------- main / cards ---------- */
main { flex: 1; overflow-y: auto; padding: 2px 12px 10px; }
main::-webkit-scrollbar { width: 0; }

.card {
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: 12px;
  margin-bottom: 10px;
  overflow: hidden;
}
.card-header {
  display: flex; align-items: center; gap: 7px;
  padding: 9px 12px 7px;
  font-weight: 600;
}
.card-icon { font-size: 13px; }
.card-title { flex: 1; font-size: 13px; }
.count-badge {
  font-family: var(--mono); font-size: 12px; font-weight: 700;
  padding: 1px 8px; border-radius: 9px;
}
.badge-ok { color: var(--green); background: rgba(48, 209, 88, 0.12); }
.badge-warn { color: var(--yellow); background: rgba(255, 214, 10, 0.12); }
.badge-neutral { color: var(--text-dim); background: rgba(255, 255, 255, 0.07); }
.card-body { padding: 2px 6px 8px; }

.empty { color: var(--text-dim); padding: 6px 8px; font-size: 12px; }

/* ---------- PR rows ---------- */
.pr-row {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 6px 8px; border-radius: 8px; cursor: pointer;
}
.pr-row:hover { background: var(--hover); }
.ci-dot {
  width: 9px; height: 9px; border-radius: 50%;
  margin-top: 4px; flex-shrink: 0;
}
.ci-dot.ok   { background: var(--green); }
.ci-dot.fail { background: var(--red); }
.ci-dot.pend { background: var(--yellow); }
.ci-dot.none { background: var(--gray); }
.pr-main { min-width: 0; flex: 1; }
.pr-line { display: flex; gap: 6px; min-width: 0; align-items: baseline; }
.pr-ref { font-weight: 600; font-size: 12px; white-space: nowrap; }
.pr-title {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 12.5px;
}
.pr-author { color: var(--text-dim); font-size: 11.5px; margin-top: 1px; }

.team-author {
  color: var(--text-dim); font-size: 11.5px; font-weight: 600;
  padding: 6px 8px 1px;
}

/* ---------- review pills ---------- */
.review-pill {
  display: flex; gap: 5px; align-items: center;
  margin: 2px 8px 4px 25px;
  padding: 4px 10px; border-radius: 8px; font-size: 11.5px;
}
.pill-ok      { background: rgba(48, 209, 88, 0.12);  color: var(--green); }
.pill-fail    { background: rgba(255, 69, 58, 0.12);  color: var(--red); }
.pill-neutral { background: rgba(255, 255, 255, 0.06); color: var(--text-dim); }
.pill-author { opacity: 0.75; }

/* ---------- error state ---------- */
.card.error { border-color: rgba(255, 69, 58, 0.4); }
.error-summary { color: var(--red); font-weight: 600; padding: 4px 8px; }
.error-detail {
  font-family: var(--mono); font-size: 11px; color: var(--text-dim);
  padding: 2px 8px; white-space: pre-wrap; word-break: break-all;
}
.retry-btn {
  margin: 8px; padding: 5px 14px; border-radius: 8px; border: none;
  background: rgba(255, 69, 58, 0.15); color: var(--red);
  font-weight: 600; cursor: pointer;
}
.retry-btn:hover { background: rgba(255, 69, 58, 0.25); }

/* ---------- footer ---------- */
footer {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px;
  border-top: 1px solid var(--card-border);
}
.footer-btn {
  background: none; border: none; color: var(--text);
  font-size: 12.5px; cursor: pointer; padding: 4px 8px; border-radius: 6px;
}
.footer-btn:hover { background: var(--hover); }
.footer-btn.primary { background: var(--purple); color: #fff; font-weight: 600; }
.footer-btn.primary:hover { filter: brightness(1.15); }
.meta { color: var(--text-dim); font-size: 11px; }

#refresh-icon { display: inline-block; }
.spinning #refresh-icon { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ---------- settings ---------- */
.pills { display: flex; gap: 6px; padding: 2px 2px 8px; }
.pill {
  padding: 4px 12px; border-radius: 12px; border: 1px solid var(--card-border);
  background: none; color: var(--text-dim); font-size: 12px; cursor: pointer;
}
.pill:hover { background: var(--hover); }
.pill.active { background: var(--purple); border-color: var(--purple); color: #fff; font-weight: 600; }
.text-input {
  width: 100%; padding: 6px 9px; border-radius: 8px;
  border: 1px solid var(--card-border); background: var(--bg);
  color: var(--text); font-size: 12.5px; outline: none;
  -webkit-user-select: text; user-select: text; resize: none;
}
.text-input:focus { border-color: var(--purple); }
.hint { color: var(--text-dim); font-size: 11px; padding-top: 5px; }
.error-banner {
  background: rgba(255, 69, 58, 0.12); color: var(--red);
  border-radius: 8px; padding: 7px 10px; font-size: 12px; margin-bottom: 10px;
}
.settings-actions { display: flex; justify-content: space-between; padding: 2px 2px 8px; }
.signin { color: var(--text-dim); font-size: 11px; text-align: center; padding-bottom: 6px; }
```

- [ ] **Step 3: Create `ui/app.js`**

```javascript
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
  state = s;
  $("view-settings").hidden = true;
  $("view-main").hidden = false;
  const cards = $("cards");
  cards.replaceChildren();

  if (!s.ok) {
    renderError(s, cards);
    updateFooter(s);
    reportHeight();
    return;
  }

  const counts = s.counts ?? {};

  const review = sectionCard("\u{1F50D}", "Waiting for your review",
    counts.review ?? 0, (counts.review ?? 0) > 0 ? "badge-warn" : "badge-ok");
  if (!s.review.length) review.body.appendChild(el("div", "empty", "All clear ✨"));
  for (const pr of s.review) review.body.appendChild(prRow(pr, { showAuthor: true }));
  cards.appendChild(review.card);

  const mine = sectionCard("\u{1F6E0}", "Your open PRs", counts.mine ?? 0, "badge-neutral");
  if (!s.mine.length) mine.body.appendChild(el("div", "empty", "No open PRs"));
  for (const pr of s.mine) {
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

  if (s.team.length) {
    const team = sectionCard("\u{1F465}", "Team", counts.team ?? 0, "badge-neutral");
    let cur = null;
    for (const pr of s.team) {
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
    const main = document.querySelector("main:not([hidden])");
    const h = document.querySelector("header").offsetHeight +
      main.scrollHeight +
      document.querySelector("footer").offsetHeight + 14;
    post({ action: "height", value: h });
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
window.settingsError = (msg) => { openSettings(); showSettingsError(msg); };

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
```

- [ ] **Step 4: Verify in browser**

Run: `open "$(pwd)/ui/index.html?demo=1"` — note: Safari may strip the query; if the page shows "Loading…", use `python3 -m http.server 8765 --directory ui` and open `http://localhost:8765/?demo=1`.

Expected: dark popover layout — header with purple glyph + clock + gear; three cards with count badges; colored CI dots; green "✓ approved · @olinoskar" pill and red "✕ changes requested" pill; hover highlight on rows; gear flips to settings (pills, textarea, Save/Back); footer with Refresh/meta/Quit.

- [ ] **Step 5: Commit**

```bash
git add ui/
git commit -m "feat: static popover UI (dark cards, pills, settings view)"
```

---

### Task 3: PyObjC shell — status item, popover, webview, ready handshake

Rewrites `app.py`. After this task the app launches, shows the popover with web UI, and falls back to a minimal NSMenu if `ui/` is missing. No data fetch yet (Task 4).

**Files:**
- Modify: `requirements.txt`
- Rewrite: `app.py`

- [ ] **Step 1: Update `requirements.txt` and install**

```
pyobjc-framework-Cocoa>=10.0
pyobjc-framework-WebKit>=10.0
py2app>=0.28.7
pytest>=8.0
```

Run: `pip install -r requirements.txt`
Expected: installs cleanly (pyobjc compiles nothing on modern macOS — wheels).

- [ ] **Step 2: Rewrite `app.py`**

Replace the entire file:

```python
"""PR Status menubar app — NSStatusItem + NSPopover + WKWebView shell.

Pure logic lives in core.py. This file is AppKit plumbing only.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAppearance,
    NSAppearanceNameVibrantDark,
    NSMenu,
    NSMenuItem,
    NSMinYEdge,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSViewController,
)
from Foundation import (
    NSMakeRect,
    NSMakeSize,
    NSObject,
    NSTimer,
    NSURL,
    NSUserNotification,
    NSUserNotificationCenter,
)
from PyObjCTools import AppHelper
from WebKit import WKWebView, WKWebViewConfiguration

import core

POPOVER_WIDTH = 360
POPOVER_MIN_HEIGHT = 200
POPOVER_MAX_HEIGHT = 640


class WebViewController(NSViewController):
    """Hosts the WKWebView; bridge messages go to the app delegate."""

    def initWithBridge_(self, bridge):
        self = objc.super(WebViewController, self).init()
        if self is None:
            return None
        self._bridge = bridge
        return self

    def loadView(self):
        config = WKWebViewConfiguration.alloc().init()
        config.userContentController().addScriptMessageHandler_name_(
            self._bridge, "app"
        )
        webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, POPOVER_WIDTH, POPOVER_MAX_HEIGHT), config
        )
        webview.setValue_forKey_(False, "drawsBackground")
        ui = core.ui_path()
        webview.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(str(ui / "index.html")),
            NSURL.fileURLWithPath_(str(ui)),
        )
        self.setView_(webview)


class AppDelegate(NSObject):
    # ---------- lifecycle ----------

    def applicationDidFinishLaunching_(self, _notification):
        self._fetching = False
        self._ui_ready = False
        self._last_state = None
        self._last_refresh = None
        self._first_run = True
        self._seen_review = set()
        self._seen_mine_reviews = {}
        self._popover = None
        self._timer = None

        cfg = core.load_app_config()
        try:
            self._poll_interval = max(
                10, int(cfg.get("poll_interval_seconds", core.DEFAULT_POLL_INTERVAL))
            )
        except (TypeError, ValueError):
            self._poll_interval = core.DEFAULT_POLL_INTERVAL

        self._status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._status_item.button().setTitle_("PR …")

        if not (core.ui_path() / "index.html").exists():
            self._install_fallback_menu("ui/ assets missing — reinstall app")
            return

        button = self._status_item.button()
        button.setTarget_(self)
        button.setAction_("togglePopover:")

        self._popover = NSPopover.alloc().init()
        self._popover.setBehavior_(NSPopoverBehaviorTransient)
        self._popover.setAppearance_(
            NSAppearance.appearanceNamed_(NSAppearanceNameVibrantDark)
        )
        self._popover.setContentSize_(NSMakeSize(POPOVER_WIDTH, 420))
        self._popover.setContentViewController_(
            WebViewController.alloc().initWithBridge_(self)
        )

        NSUserNotificationCenter.defaultUserNotificationCenter().setDelegate_(self)

        self._restart_timer()
        self.startFetch_(None)

    def _install_fallback_menu(self, message: str) -> None:
        """Never leave the app unquittable: plain menu with error + Quit."""
        self._status_item.button().setTitle_("PR ⚠️")
        menu = NSMenu.alloc().init()
        menu.addItem_(
            NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(message, None, "")
        )
        menu.addItem_(
            NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit", "terminate:", "q"
            )
        )
        self._status_item.setMenu_(menu)

    def _restart_timer(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
        self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            float(self._poll_interval), self, "startFetch:", None, True
        )

    # ---------- popover ----------

    def togglePopover_(self, _sender):
        if self._popover.isShown():
            self._popover.close()
            return
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        button = self._status_item.button()
        self._popover.showRelativeToRect_ofView_preferredEdge_(
            button.bounds(), button, NSMinYEdge
        )

    # ---------- fetch pipeline (Task 4 fills these in) ----------

    def startFetch_(self, _sender):
        pass

    # ---------- JS bridge ----------

    def userContentController_didReceiveScriptMessage_(self, _ucc, message):
        body = message.body()
        try:
            action = body.get("action")
        except AttributeError:
            return
        if action == "ready":
            self._ui_ready = True
            if self._last_state is not None:
                self._render(self._last_state)

    def _render(self, state: dict) -> None:
        self._last_state = state
        if not self._ui_ready or self._popover is None:
            return
        js = f"window.render({json.dumps(state)})"
        webview = self._popover.contentViewController().view()
        webview.evaluateJavaScript_completionHandler_(js, None)

    def _push_js(self, js: str) -> None:
        if not self._ui_ready or self._popover is None:
            return
        webview = self._popover.contentViewController().view()
        webview.evaluateJavaScript_completionHandler_(js, None)

    # ---------- notifications (Task 6 fills these in) ----------

    def userNotificationCenter_shouldPresentNotification_(self, _center, _note):
        return True


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    if "--check" in sys.argv:
        result = subprocess.run(
            [str(core.script_path()), "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=core.SCRIPT_TIMEOUT_SECONDS,
            env=core.subprocess_env(),
        )
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            sys.exit(result.returncode)
        json.loads(result.stdout)
        print("OK")
        sys.exit(0)
    main()
```

Notes for the implementer:
- `app.setDelegate_` holds a **weak** reference in ObjC; the `delegate` local in `main()` keeps it alive because `main()` never returns (event loop). Don't refactor that away.
- `NSMinYEdge` makes the popover appear below the status item.
- `setValue_forKey_(False, "drawsBackground")` prevents a white flash before CSS paints; if a future macOS removes that KVC key (it throws `NSUnknownKeyException`), wrap in try/except and accept the flash.
- Bridge `message.body()` arrives as `NSDictionary`; PyObjC gives it a dict-like `.get`.

- [ ] **Step 3: Run and verify manually**

Run: `python3 app.py`
Expected: "PR …" appears in the menubar. Click → dark popover with "Loading…" (no data yet). Click outside → closes. `Ctrl-C` in terminal quits.

Also verify fallback: `mv ui ui.bak && python3 app.py` → "PR ⚠️" with menu showing error + Quit. Then `mv ui.bak ui`.

- [ ] **Step 4: Run tests (regression)**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS (core untouched).

- [ ] **Step 5: Commit**

```bash
git add app.py requirements.txt
git commit -m "feat: PyObjC popover shell with WKWebView (replaces rumps menu)"
```

---

### Task 4: Background fetch pipeline + render + error state + counts

**Files:**
- Modify: `app.py` (fill in fetch section)

- [ ] **Step 1: Implement fetch pipeline**

Replace the `startFetch_` stub in `app.py` with:

```python
    def startFetch_(self, _sender):
        if self._fetching:
            return
        self._fetching = True
        self._push_js("window.setRefreshing(true)")
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self) -> None:
        """Runs on a background thread; never touch AppKit from here."""
        try:
            result = subprocess.run(
                [str(core.script_path()), "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=core.SCRIPT_TIMEOUT_SECONDS,
                check=True,
                env=core.subprocess_env(),
            )
            data = json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            AppHelper.callAfter(
                self._apply_error, f"Script failed (rc={e.returncode})", e.stderr or ""
            )
            return
        except subprocess.TimeoutExpired:
            AppHelper.callAfter(self._apply_error, "Script timeout", "")
            return
        except FileNotFoundError as e:
            AppHelper.callAfter(self._apply_error, "Script not found", str(e))
            return
        except json.JSONDecodeError as e:
            AppHelper.callAfter(self._apply_error, "Bad JSON from script", str(e))
            return
        AppHelper.callAfter(self._apply_data, data)

    def _apply_data(self, data: dict) -> None:
        self._fetching = False
        self._last_refresh = datetime.now().strftime("%H:%M:%S")
        counts = data.get("counts", {})
        self._status_item.button().setTitle_(
            f"🔍 {counts.get('review', 0)}  🛠️ {counts.get('mine', 0)}"
        )
        state = core.build_state(
            data,
            last_refresh=self._last_refresh,
            poll_interval=self._poll_interval,
            teammates=core.load_pr_config()["teammates"],
        )
        self._render(state)
        if not self._first_run:
            for note in core.compute_notifications(
                data, self._seen_review, self._seen_mine_reviews
            ):
                self._deliver_notification(note)
        self._seen_review, self._seen_mine_reviews = core.update_seen(data)
        self._first_run = False

    def _apply_error(self, summary: str, detail: str) -> None:
        self._fetching = False
        self._status_item.button().setTitle_("PR ⚠️")
        self._render(core.error_state(summary, detail))

    def _deliver_notification(self, note: dict) -> None:
        pass  # Task 6
```

- [ ] **Step 2: Run and verify manually**

Run: `python3 app.py`
Expected: menubar title becomes `🔍 N  🛠️ M` within ~10s. Popover shows real PRs in three cards with CI dots and review pills. UI stays responsive while the script runs (open popover immediately after launch — no beachball).

Error path: `PR_STATUS_CONFIG= mv ~/.config/pr-status/config ~/.config/pr-status/config.bak; python3 app.py` → red error card with Retry; restore with `mv ~/.config/pr-status/config.bak ~/.config/pr-status/config`. (If the script exits 0 with empty sections instead of failing, simulate failure by temporarily pointing `script_path` at a script that `exit 1`s — easiest: `chmod -x pr-status` triggers the FileNotFoundError/PermissionError path, then `chmod +x pr-status`.)

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: background fetch pipeline with live render and error state"
```

---

### Task 5: Bridge actions — openPR, refresh, quit, height resize

**Files:**
- Modify: `app.py` (extend `userContentController_didReceiveScriptMessage_`)

- [ ] **Step 1: Extend the bridge dispatcher**

Replace the bridge method body in `app.py`:

```python
    def userContentController_didReceiveScriptMessage_(self, _ucc, message):
        body = message.body()
        try:
            action = body.get("action")
        except AttributeError:
            return
        if action == "ready":
            self._ui_ready = True
            if self._last_state is not None:
                self._render(self._last_state)
        elif action == "openPR":
            url = body.get("url")
            if url and url != "#":
                webbrowser.open(url)
            self._popover.close()
        elif action == "refresh":
            self.startFetch_(None)
        elif action == "quit":
            NSApplication.sharedApplication().terminate_(None)
        elif action == "height":
            try:
                h = float(body.get("value", 0))
            except (TypeError, ValueError):
                return
            h = max(POPOVER_MIN_HEIGHT, min(POPOVER_MAX_HEIGHT, h))
            self._popover.setContentSize_(NSMakeSize(POPOVER_WIDTH, h))
        elif action == "saveSettings":
            self._save_settings(body)

    def _save_settings(self, body) -> None:
        pass  # Task 7
```

- [ ] **Step 2: Run and verify manually**

Run: `python3 app.py`
Expected: click a PR row → opens in browser, popover closes. ⟳ Refresh → spinner spins, data reloads, spinner stops. Quit button → app exits. Popover height hugs content (short when few PRs, capped at 640 with inner scroll when many).

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: bridge actions — open PR, refresh, quit, content-driven height"
```

---

### Task 6: Notifications

**Files:**
- Modify: `app.py` (fill in `_deliver_notification`, add click delegate)

- [ ] **Step 1: Implement delivery + click handling**

Replace the `_deliver_notification` stub and add the activate delegate next to `userNotificationCenter_shouldPresentNotification_`:

```python
    def _deliver_notification(self, note: dict) -> None:
        n = NSUserNotification.alloc().init()
        n.setTitle_(note["title"])
        n.setSubtitle_(note["subtitle"])
        n.setInformativeText_(note["message"])
        n.setUserInfo_({"url": note.get("url", "")})
        NSUserNotificationCenter.defaultUserNotificationCenter().deliverNotification_(n)

    def userNotificationCenter_didActivateNotification_(self, _center, notification):
        url = (notification.userInfo() or {}).get("url")
        if url:
            webbrowser.open(url)
```

- [ ] **Step 2: Verify manually (forced diff)**

Run: `python3 app.py`, wait for first load, then temporarily clear seen-state to force notifications on next poll — in a second terminal trigger refresh via the UI after editing `app.py` is NOT needed; instead test with a one-off hack: stop the app, set `self._first_run = False` and `self._seen_review = set()` won't survive restart, so the practical check is: leave the app running until a teammate actually pushes a PR or a reviewer acts, OR temporarily add `self._first_run = False` right before the first `self._apply_data` call (line in `applicationDidFinishLaunching_`: change nothing else), launch — every current review request fires a notification. Revert the hack after confirming.

Expected: banner notifications appear (dev mode: under the Python interpreter's identity — known limitation, correct in the bundled .app). Clicking one opens the PR.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: native notifications for review requests and reviewer changes"
```

---

### Task 7: Settings persistence

**Files:**
- Modify: `app.py` (fill in `_save_settings`)

- [ ] **Step 1: Implement `_save_settings`**

```python
    def _save_settings(self, body) -> None:
        try:
            secs = max(10, int(body.get("poll_interval_seconds", self._poll_interval)))
        except (TypeError, ValueError):
            secs = self._poll_interval
        teammates = [
            str(t).strip() for t in (body.get("teammates") or []) if str(t).strip()
        ]
        self._poll_interval = secs
        cfg = core.load_app_config()
        cfg["poll_interval_seconds"] = secs
        try:
            core.save_app_config(cfg)
            core.save_teammates(teammates)
        except OSError as e:
            self._push_js(f"window.settingsError({json.dumps(str(e))})")
            return
        self._restart_timer()
        self.startFetch_(None)
```

- [ ] **Step 2: Verify manually**

Run: `python3 app.py`
Expected:
- Gear → settings. Pick "1m", Save → returns to main view, refetches; `cat ~/.config/pr-status/app.json` shows `"poll_interval_seconds": 60`.
- Custom field `90s` → Save → `app.json` shows 90.
- Custom `5x` → inline red error banner, nothing saved.
- Edit teammates list, Save → `~/.config/pr-status/config` `[teammates]` block rewritten, `orgs=` line preserved; Team card updates after refetch.
- Relaunch app → interval persisted.

- [ ] **Step 3: Run tests (regression)**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: in-popover settings — refresh interval and teammates"
```

---

### Task 8: Packaging, README, final verification

**Files:**
- Modify: `setup.py`
- Modify: `README.md`

- [ ] **Step 1: Update `setup.py`**

```python
"""py2app build configuration for PR Status menubar app.

Build:    python3 setup.py py2app
Dev run:  python3 setup.py py2app -A   (alias mode, live source)
Output:   dist/PR Status.app
"""
from setuptools import setup

APP = ["app.py"]
DATA_FILES = ["pr-status"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "PR Status",
        "CFBundleDisplayName": "PR Status",
        "CFBundleIdentifier": "com.github.dtorsson.pr-status",
        "CFBundleVersion": "0.2.0",
        "CFBundleShortVersionString": "0.2.0",
        "LSUIElement": True,
        "NSHumanReadableCopyright": "",
        "NSUserNotificationAlertStyle": "banner",
    },
    "includes": ["core"],
    "resources": ["pr-status", "ui"],
}

setup(
    name="PR Status",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
```

(`"packages": ["rumps"]` removed; py2app's pyobjc recipe picks up AppKit/WebKit automatically. `"resources": ["ui"]` copies the directory into `Resources/ui/`, which `core.ui_path()` resolves.)

- [ ] **Step 2: Build and verify the bundle**

```bash
rm -rf build dist
python3 setup.py py2app
open "dist/PR Status.app"
```

Expected: app launches from the bundle; popover renders; PR rows open browser; notifications now appear under "PR Status" identity. Check `dist/PR Status.app/Contents/Resources/ui/index.html` exists.

- [ ] **Step 3: Update `README.md`**

Rewrite the affected sections (keep Prerequisites/Configuration/launch-at-login as is):
- **Features**: replace "dropdown"/menu wording with popover description — dark card UI, CI dots, review-state pills, hover-to-open rows, in-popover settings (gear icon), content-sized popover.
- **Files table**: `app.py` → "PyObjC shell: status item, popover, WKWebView bridge, notifications"; add `core.py` → "Pure logic (config, intervals, state, notification diffing) — pytest-covered"; add `ui/` → "Popover web UI (HTML/CSS/JS)"; add `tests/`.
- **Quick start (dev)**: add `python3 -m pytest tests/` and the browser demo command `python3 -m http.server 8765 --directory ui` → `http://localhost:8765/?demo=1`.
- **Settings storage** table: unchanged paths, but "Managed by" → "Preferences panel in the popover (gear icon)".
- Remove the rumps mention in the notes.

- [ ] **Step 4: Full manual checklist**

- Popover open/close via status item; closes on outside click.
- All three sections render with real data; Team grouped by author.
- Empty states ("All clear ✨" / "No open PRs") when applicable.
- Click PR → browser; Refresh spins; Quit works (both dev and bundle).
- Settings: interval preset + custom + teammates roundtrip; persists across relaunch.
- Error card + Retry on script failure; `PR ⚠️` title.
- `python3 app.py --check` prints `OK`.
- `python3 -m pytest tests/ -v` all green.

- [ ] **Step 5: Commit**

```bash
git add setup.py README.md
git commit -m "chore: package ui/ resources, bump to 0.2.0, update README"
```

---

## Self-review notes

- Spec coverage: architecture (T3–T5), background-thread fix (T4), visual design (T2), settings view (T2+T7), notifications (T6), fallback menu (T3), `--check` kept (T3), packaging/README (T8). Light-mode, tabs, org editing: out of scope per spec.
- `REVIEW_STATE_DISPLAY`/`CI_DOTS` intentionally not ported — JS owns presentation (`CI_CLASS`, `REVIEW_PILLS`).
- Old `app.py` keeps working until its Task 3 rewrite; every task ends green.
