# PR Status — macOS menubar app

Menubar wrapper around the `pr-status` bash script. Shows pending review
requests, your own open PRs, and (optionally) teammate PRs in a dark
popover anchored to the menubar.

## Features

- Dark card-style popover with three sections: **Waiting for your
  review**, **Your open PRs**, **Team** (only when teammates are
  configured). The popover sizes itself to its content (capped with
  inner scrolling).
- Menubar title shows live counts: `🔍 N  🛠️ M`
  (review requests / your open PRs).
- Colored CI status dot per PR — green success · red failure · yellow
  in progress · gray no checks.
- Review-state pills on your own PRs — ✓ approved · ✕ changes
  requested · 💬 commented · ⌛ pending — with the reviewer's handle.
- Every PR row is clickable — opens the PR in the browser.
- Native macOS notifications on:
  - A new review request landing on you.
  - A reviewer flipping to approve / changes-requested / commented on
    one of your PRs.
- Last refresh time + current auto-refresh interval shown in the
  popover footer.
- In-popover settings via the gear icon:
  - **Refresh every** — preset pills (1 / 5 / 10 / 30 min) or a custom
    `90s` / `3m` / `1h30m` field. Persisted across launches.
  - **Teammates** — textarea that rewrites the `[teammates]` section
    of `~/.config/pr-status/config`. Empty input hides the Team
    section.

## Prerequisites

- macOS
- `gh` (GitHub CLI) authenticated: `gh auth login`
- `jq`
- Python 3.10+

```bash
brew install gh jq
```

## Configuration

The bash script reads `~/.config/pr-status/config`. Create it before
first run:

```ini
orgs=my-org,other-org
max_age_days=21

[teammates]
alice
bob
carol
```

`max_age_days` controls how far back to look when fetching teammate PRs
(default 21). The menubar app inherits this config — orgs and
`max_age_days` are edit-by-hand; teammates can also be managed via the
gear-icon preferences panel in the popover.

## Quick start (dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the unit tests
python3 -m pytest tests/

# Sanity-check the script works and emits valid JSON
python3 app.py --check

# Run the menubar app from source
python3 app.py

# Preview the popover UI in a browser (no native shell needed)
python3 -m http.server 8765 --directory ui
# then open http://localhost:8765/?demo=1
```

> Dev-mode notifications appear under the Python interpreter's identity.
> Build the bundled `.app` (below) for proper notification sender +
> grouping.

## Build the `.app`

```bash
source .venv/bin/activate
rm -rf build dist
python3 setup.py py2app
open "dist/PR Status.app"
```

Move `dist/PR Status.app` into `/Applications` to keep it around.

### Launch at login

System Settings → General → Login Items → add `PR Status.app`.

### Notifications

First launch will prompt for permission. If you missed it:
System Settings → Notifications → PR Status → enable.

The bundle uses identifier `com.github.dtorsson.pr-status`, so
notifications group under "PR Status" with a stable sender.

## Files

| File | Purpose |
|---|---|
| `pr-status` | Bash script. Default: terminal render. With `--json`: machine-readable output (also emits a CI rollup per PR). |
| `app.py` | PyObjC shell: status item, popover, WKWebView bridge, notifications. |
| `core.py` | Pure logic (config, intervals, state, notification diffing) — pytest-covered. |
| `ui/` | Popover web UI (HTML/CSS/JS). |
| `tests/` | pytest suite for `core.py`. |
| `setup.py` | py2app bundle config. |
| `requirements.txt` | Python deps (PyObjC, `py2app`, `pytest`). |

## Settings storage

| Setting | Location | Managed by |
|---|---|---|
| `orgs`, `max_age_days`, `[teammates]` | `~/.config/pr-status/config` | Script reads on every run. Teammates editable via the gear-icon preferences panel. |
| Refresh interval | `~/.config/pr-status/app.json` | Set via the gear-icon preferences panel. |

## Tuning

- Refresh interval — gear-icon preferences panel (or edit
  `~/.config/pr-status/app.json`).
- Script timeout — `SCRIPT_TIMEOUT_SECONDS` in `core.py`.
- Org list / `max_age_days` — `~/.config/pr-status/config`.

## Notes

- CI rollup adds one `gh pr view` per PR (run in parallel with existing
  per-PR fetches). Heavy GH API users may want to lengthen the refresh
  interval.
- The bash script also runs standalone in a terminal with colored
  output and OSC8 hyperlinks; the `--json` mode is only used by the
  menubar app.
