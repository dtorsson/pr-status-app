# PR Status — Custom Popover UI Redesign

**Date:** 2026-06-12
**Status:** Approved design, pending implementation plan

## Goal

Replace the plain `NSMenu` dropdown (rumps) with a rich custom popover styled
after the "Claude God" menubar app: dark theme, rounded cards, colored status
dots and pills, hover states, in-popover settings.

## Constraints

- Keep the existing data layer: the `pr-status` bash script invoked with
  `--json`, config at `~/.config/pr-status/config`, app settings at
  `~/.config/pr-status/app.json`. No changes to the bash script.
- Keep py2app bundling (`setup.py`) and the `--check` CLI mode.
- Keep native macOS notifications with identical trigger semantics
  (new review request; reviewer state change on own PRs).
- macOS only, Python 3.10+.

## Architecture

### Stack

- **Drop `rumps`.** Pure PyObjC application shell:
  - `NSApplication` with `LSUIElement` (no Dock icon — already set in
    `setup.py` plist or to be added).
  - `NSStatusItem` whose button title keeps the live counts text
    (`🔍 3  🛠 4`), same as today.
  - Clicking the status item toggles an `NSPopover`
    (`NSPopoverBehaviorTransient` — closes on outside click) anchored to the
    status item button.
  - The popover's content view controller hosts a `WKWebView`.
- **UI is web content**: static files in a new `ui/` directory —
  `index.html`, `style.css`, `app.js`. Loaded once via
  `loadFileURL:allowingReadAccessToURL:`.

### Data flow

1. A repeating timer fires every `poll_interval_seconds` (existing setting,
   min 10s, default 300s).
2. Fetch runs on a **background thread** (`threading.Thread` or
   `dispatch_async` to a global queue): `subprocess.run([script, "--json"])`
   with the existing 120s timeout and PATH-augmented env.
   This fixes the current bug where the script blocks the main thread.
3. On completion, dispatch to the main thread:
   - Update status item title with counts.
   - Build a state dict and push it to the webview:
     `evaluateJavaScript("window.render(<json>)")`.
   - Run notification diffing (same `_seen_review` /
     `_seen_mine_reviews` logic as today).
4. Only one fetch may be in flight at a time; a fetch triggered while one is
   running is ignored.

State pushed to JS (superset of script output):

```json
{
  "ok": true,
  "review": [...], "mine": [...], "team": [...],
  "counts": {"review": 3, "mine": 4, "team": 9},
  "me": "dtorsson", "orgs": "annotell,kognic-internal",
  "last_refresh": "12:36:40",
  "poll_interval_label": "5m",
  "refreshing": false,
  "settings": {
    "poll_interval_seconds": 300,
    "teammates": ["olinoskar", "..."]
  }
}
```

Error state: `{"ok": false, "error_summary": "...", "error_detail": "..."}`.

### JS → Python bridge

`WKScriptMessageHandler` registered as `window.webkit.messageHandlers.app`.
Messages are `{action: string, ...payload}`:

| Action | Payload | Effect |
|---|---|---|
| `openPR` | `{url}` | `webbrowser.open(url)`, close popover |
| `refresh` | — | trigger fetch now (sets `refreshing: true` immediately) |
| `quit` | — | terminate app |
| `saveSettings` | `{poll_interval_seconds, teammates: [..]}` | persist both (existing `save_app_config` / `save_teammates`), restart timer, refetch |

Settings validation happens in JS (interval ≥ 10s, parse `90s/3m/1h30m`
formats by porting `parse_interval` to JS); Python re-validates and clamps as
a backstop.

### Notifications

Use `NSUserNotificationCenter` via PyObjC directly (the same deprecated-but-
functional API rumps wraps). Clicking a notification opens the PR URL
(`userInfo["url"]`). Delegate method
`userNotificationCenter:shouldPresentNotification:` returns `True` so banners
show while the app is frontmost.

## Visual design

Popover: width 360px, height content-driven, max ~600px then inner scroll.
Dark theme regardless of system appearance (matches Claude God).

Palette:
- Background `#1d1d1f`, cards `#2a2a2c`, card border `1px rgba(255,255,255,.08)`,
  radius 12px.
- Text: primary `#e8e8ea`, secondary `#98989d`.
- Accents: green `#30d158`, red `#ff453a`, yellow `#ffd60a`, gray `#636366`,
  purple `#7d56f4` (app glyph).
- Fonts: `-apple-system` for text; `ui-monospace` (SF Mono) for repo`#num`,
  counts, and times.

### Layout (top to bottom)

1. **Header**: purple rounded-square glyph with "P", title "PR Status" with
   small green online dot, right side: current time (HH:MM, mono) and gear
   button.
2. **Section cards** (one per section, in order):
   - 🔍 *Waiting for your review* — count badge right-aligned (green when 0,
     yellow when >0).
   - 🛠 *Your open PRs* — neutral count badge.
   - 👥 *Team* — only rendered when teammates configured; grouped by
     `@author` subheaders inside the card.
   - Empty states inside cards: "All clear ✨" / "No open PRs", dimmed.
3. **PR row** (clickable, hover background `rgba(255,255,255,.06)`):
   - CSS circle CI dot: green=success, red=failure, yellow=pending,
     gray=none.
   - `repo #123` in mono semi-bold, then title (single line, ellipsis).
   - Second line (dim, smaller): `@author` (review + team rows only).
4. **Review-state pills** under own PRs: full-width tinted rows like Claude
   God's "Active session" row — green tint for approved, red tint for
   changes-requested, neutral for commented/pending. Text:
   `✅ approved · @olinoskar`.
5. **Footer** (separated by hairline): ⟳ Refresh button (spins while
   `refreshing`), center: `last refresh 12:36:40` + `every 5m` (dim, mono),
   right: Quit.

### Settings view

Gear toggles the popover content to a settings panel (same window size,
slide or instant swap — implementer's choice, instant is fine):

- **Refresh interval**: preset pill buttons (1m / 5m / 10m / 30m) +
  text field for custom (`90s`, `3m`, `1h30m`). Active choice highlighted.
- **Teammates**: textarea, one username per line (or comma-separated —
  accept both). Empty disables Team section.
- **Save** and **Back** buttons. Save persists, returns to main view,
  triggers refresh.
- Footer of settings view: `Signed in as @dtorsson · orgs` (read-only info
  that currently lives in the menu).

### Error state

When `ok: false`: red-tinted card with summary, detail lines (max 5, mono,
small), and a Retry button. Footer remains so Quit is always reachable.

## File changes

| File | Change |
|---|---|
| `app.py` | Rewritten: PyObjC app shell, status item, popover, webview bridge, background fetch, notifications. Reuses existing helpers (`parse_interval`, `format_interval`, config load/save, `script_path`, `subprocess_env`) unchanged. |
| `ui/index.html`, `ui/style.css`, `ui/app.js` | New. All rendering and the settings panel. |
| `setup.py` | Bundle `ui/` into Resources alongside `pr-status`. Drop rumps from deps; PyObjC packages instead. |
| `requirements.txt` | `rumps` → `pyobjc-framework-Cocoa`, `pyobjc-framework-WebKit`. |
| `README.md` | Update features, screenshots section, dev instructions. |

`script_path()` gains a sibling: `ui_path()` resolving `ui/` in dev
(next to `app.py`) or bundled (`Resources/ui/`).

## Error handling

- Script failure/timeout/bad JSON → error state in webview + `PR ⚠️` status
  item title (same as today).
- Webview load failure (missing `ui/` dir) → fall back to a minimal `NSMenu`
  with the error and Quit, so the app is never unquittable.
- Config write failures → JS alert banner inside settings view with the
  OS error message.

## Testing

- `python3 app.py --check` unchanged (script sanity check).
- New: `python3 -m pytest` not introduced — manual testing per README.
  Pure-Python helpers (`parse_interval` etc.) keep working unchanged.
- Manual checklist: popover open/close, all three sections render, click PR
  opens browser, hover states, settings save + persist across relaunch,
  notifications fire on change, error state on broken config, bundled .app
  via py2app behaves identically.

## Out of scope

- Tabs / multiple views beyond main + settings.
- Changes to the bash script or its JSON shape.
- Light-mode theme.
- Editing `orgs` / `max_age_days` from the UI (stays edit-by-hand).
