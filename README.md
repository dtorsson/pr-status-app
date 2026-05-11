# PR Status — macOS menubar app

Menubar wrapper around the `pr-status` bash script. Shows pending review
requests and your open PRs in the menubar, opens PRs on click, and fires
native macOS notifications when:

- A new review is requested from you
- A reviewer approves, requests changes, or comments on one of your PRs

## Prerequisites

- macOS
- `gh` (GitHub CLI) authenticated: `gh auth login`
- `jq`
- Python 3.10+

```bash
brew install gh jq
```

## Quick start (dev)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Sanity-check the script works and emits valid JSON
python3 app.py --check

# Run the menubar app from source
python3 app.py
```

The app appears in the menubar as `👁 N  ⊙ M` (reviews waiting / your open
PRs). Click any PR to open it in the browser. It polls every 5 minutes.

## Build a real `.app`

```bash
source .venv/bin/activate
rm -rf build dist
python3 setup.py py2app
open "dist/PR Status.app"
```

Move `dist/PR Status.app` into `/Applications` if you want it permanent.

### Launch at login

System Settings → General → Login Items → add `PR Status.app`.

### Notifications

First launch will prompt for Notification permission. If you missed it:
System Settings → Notifications → PR Status → enable.

The app bundle has its own identifier (`com.github.dtorsson.pr-status`) so
notifications appear under "PR Status" with a proper sender (not "Script
Editor"). Running `python3 app.py` directly will show notifications under
the Python interpreter — that's expected for dev mode.

## Configuration

The bash script reads `~/.config/pr-status/config`. Create it before first
run:

```ini
orgs=my-org,other-org
max_age_days=21

[teammates]
alice
bob
carol
```

The menubar app inherits that config. The "Edit teammates…" item in the
Preferences submenu rewrites the `[teammates]` section.

## Files

| File | Purpose |
|---|---|
| `pr-status` | Bash script. Default: terminal render. With `--json`: machine-readable output. |
| `app.py` | rumps menubar app. Polls the script, renders menu, fires notifications. |
| `setup.py` | py2app bundle config. |
| `requirements.txt` | Python deps (`rumps`, `py2app`). |

## Tuning

- Poll interval — edit `POLL_INTERVAL_SECONDS` in `app.py` (default 300s).
- Script timeout — `SCRIPT_TIMEOUT_SECONDS` in `app.py`.
- Org list / teammates — `~/.config/pr-status/config`.
