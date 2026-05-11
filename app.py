"""PR Status menubar app — polls pr-status script, notifies on changes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import rumps

DEFAULT_POLL_INTERVAL = 300
SCRIPT_TIMEOUT_SECONDS = 120
PRESET_INTERVALS: list[tuple[int, str]] = [
    (60, "1 min"),
    (300, "5 min"),
    (600, "10 min"),
    (1800, "30 min"),
]

CONFIG_DIR = Path.home() / ".config" / "pr-status"
PR_CONFIG_PATH = CONFIG_DIR / "config"
APP_CONFIG_PATH = CONFIG_DIR / "app.json"
TEAMMATES_HEADER = "[teammates]"

REVIEW_STATE_DISPLAY = {
    "APPROVED": ("✅", "approved"),
    "CHANGES_REQUESTED": ("🛑", "requested changes"),
    "COMMENTED": ("💬", "commented"),
    "PENDING": ("⌛", "pending"),
}
CI_DOTS = {
    "success": "🟢",
    "failure": "🔴",
    "pending": "🟡",
    "none": "⚪",
}
REVIEW_STATE_LABELS = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "requested changes",
    "COMMENTED": "commented",
}


def script_path() -> Path:
    """Locate pr-status script in dev (next to app.py) or bundled (Resources/)."""
    here = Path(__file__).resolve().parent
    candidates = [here / "pr-status", here.parent / "Resources" / "pr-status"]
    for c in candidates:
        if c.exists():
            return c
    return here / "pr-status"


def format_interval(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m" if s == 0 else f"{m}m {s}s"
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h" if m == 0 else f"{h}h {m}m"


def parse_interval(text: str) -> int:
    """Parse '90s', '3m', '1h', '1h30m', or plain seconds. Returns seconds."""
    text = text.strip().lower()
    if not text:
        raise ValueError("empty input")
    if text.isdigit():
        return int(text)
    total = 0
    num = ""
    for ch in text:
        if ch.isdigit():
            num += ch
        elif ch in ("h", "m", "s"):
            if not num:
                raise ValueError("missing number before unit")
            total += {"h": 3600, "m": 60, "s": 1}[ch] * int(num)
            num = ""
        elif ch.isspace():
            continue
        else:
            raise ValueError(f"unknown character: {ch!r}")
    if num:
        raise ValueError("number without unit")
    if total <= 0:
        raise ValueError("interval must be positive")
    return total


def load_app_config() -> dict:
    if APP_CONFIG_PATH.exists():
        try:
            return json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return {}


def save_app_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    APP_CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_pr_config(path: Path = PR_CONFIG_PATH) -> dict:
    """Parse pr-status config file. Returns {'top': [lines], 'teammates': [logins]}."""
    result: dict[str, list[str]] = {"top": [], "teammates": []}
    if not path.exists():
        return result
    in_teammates = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line == TEAMMATES_HEADER:
            in_teammates = True
            continue
        if line.startswith("[") and line.endswith("]"):
            in_teammates = False
            continue
        if in_teammates:
            result["teammates"].append(line)
        else:
            result["top"].append(line)
    return result


def save_teammates(new_teammates: list[str], path: Path = PR_CONFIG_PATH) -> None:
    """Rewrite config preserving top-level lines, replacing [teammates] block."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_pr_config(path)
    lines = list(existing["top"])
    if new_teammates:
        lines.append(TEAMMATES_HEADER)
        lines.extend(new_teammates)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def subprocess_env() -> dict[str, str]:
    """Ensure gh / jq / git resolve when launched outside a shell."""
    env = os.environ.copy()
    extra_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    existing = env.get("PATH", "")
    env["PATH"] = ":".join(extra_paths + ([existing] if existing else []))
    return env


class PRStatusApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("PR …", quit_button=None)
        self._first_run = True
        self._seen_review: set[tuple[str, int]] = set()
        self._seen_mine_reviews: dict[tuple[str, int], dict[str, str]] = {}
        self._url_by_id: dict[str, str] = {}
        self._last_refresh: datetime | None = None
        cfg = load_app_config()
        try:
            self._poll_interval = max(10, int(cfg.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)))
        except (TypeError, ValueError):
            self._poll_interval = DEFAULT_POLL_INTERVAL
        self.menu = ["Loading…"]
        self._timer = rumps.Timer(self._tick, self._poll_interval)
        self._timer.start()
        rumps.Timer(self._kick_initial, 1).start()

    def _kick_initial(self, sender: rumps.Timer) -> None:
        sender.stop()
        self._tick(None)

    def _tick(self, _sender) -> None:
        try:
            data = self._fetch()
        except subprocess.CalledProcessError as e:
            self._render_error(f"Script failed (rc={e.returncode})", e.stderr or "")
            return
        except subprocess.TimeoutExpired:
            self._render_error("Script timeout", "")
            return
        except FileNotFoundError as e:
            self._render_error("Script not found", str(e))
            return
        except json.JSONDecodeError as e:
            self._render_error("Bad JSON from script", str(e))
            return

        self._last_refresh = datetime.now()
        self._render(data)
        if not self._first_run:
            self._notify_changes(data)
        self._update_seen(data)
        self._first_run = False

    def _fetch(self) -> dict:
        result = subprocess.run(
            [str(script_path()), "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SCRIPT_TIMEOUT_SECONDS,
            check=True,
            env=subprocess_env(),
        )
        return json.loads(result.stdout)

    def _render(self, data: dict) -> None:
        counts = data["counts"]
        self.title = f"🔍 {counts['review']}  🛠️ {counts['mine']}"

        self.menu.clear()
        self._url_by_id.clear()

        self._add_section_header(f"🔍  Waiting for your review ({counts['review']})")
        if not data["review"]:
            self.menu.add(self._disabled("    All clear ✨"))
        for pr in data["review"]:
            self._add_pr_item(
                pr,
                label=self._format_review_item(pr),
            )

        self.menu.add(rumps.separator)
        self._add_section_header(f"🛠️  Your open PRs ({counts['mine']})")
        if not data["mine"]:
            self.menu.add(self._disabled("    No open PRs"))
        for pr in data["mine"]:
            self._add_pr_item(pr, label=self._format_mine_item(pr))
            for review in pr.get("reviews", []) or []:
                state = review.get("state", "")
                icon, label = REVIEW_STATE_DISPLAY.get(
                    state, ("○", state.lower().replace("_", " ") if state else "review")
                )
                author = review.get("author", "?")
                self.menu.add(
                    self._disabled(f"          {icon}  {label} · @{author}")
                )

        if data["team"]:
            self.menu.add(rumps.separator)
            self._add_section_header(f"👥  Team ({counts['team']})")
            current_author: str | None = None
            for pr in data["team"]:
                author = pr.get("author", {}).get("login", "?")
                if author != current_author:
                    self.menu.add(self._disabled(f"    @{author}"))
                    current_author = author
                self._add_pr_item(pr, label=self._format_team_item(pr), indent="        ")

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem(f"Signed in as @{data.get('me', '?')}"))
        self.menu.add(self._disabled(f"Orgs: {data.get('orgs', '')}"))
        refresh_label = (
            f"Last refresh: {self._last_refresh.strftime('%H:%M:%S')}"
            if self._last_refresh
            else "Last refresh: —"
        )
        self.menu.add(self._disabled(refresh_label))
        self.menu.add(
            self._disabled(f"Auto-refresh: every {format_interval(self._poll_interval)}")
        )
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Refresh now", callback=self._tick, key="r"))
        self.menu.add(self._build_prefs_menu())
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application, key="q"))

    def _build_prefs_menu(self) -> rumps.MenuItem:
        prefs = rumps.MenuItem("Preferences")

        refresh_menu = rumps.MenuItem("Refresh every")
        for secs, label in PRESET_INTERVALS:
            item = rumps.MenuItem(label, callback=self._make_set_interval(secs))
            item.state = 1 if secs == self._poll_interval else 0
            refresh_menu.add(item)
        custom_label = "Custom…"
        if not any(secs == self._poll_interval for secs, _ in PRESET_INTERVALS):
            custom_label = f"Custom… ({format_interval(self._poll_interval)})"
        custom_item = rumps.MenuItem(custom_label, callback=self._prompt_custom_interval)
        if not any(secs == self._poll_interval for secs, _ in PRESET_INTERVALS):
            custom_item.state = 1
        refresh_menu.add(custom_item)
        prefs.add(refresh_menu)

        teammates_menu = rumps.MenuItem("Teammates")
        team_count = len(load_pr_config()["teammates"])
        teammates_menu.add(
            rumps.MenuItem(
                f"Edit teammates… ({team_count})",
                callback=self._prompt_edit_teammates,
            )
        )
        teammates_menu.add(rumps.MenuItem("Reload from config", callback=self._tick))
        prefs.add(teammates_menu)

        return prefs

    def _make_set_interval(self, secs: int):
        def handler(_):
            self._set_poll_interval(secs)

        return handler

    def _set_poll_interval(self, secs: int) -> None:
        secs = max(10, int(secs))
        self._poll_interval = secs
        try:
            self._timer.stop()
        except Exception:
            pass
        self._timer = rumps.Timer(self._tick, secs)
        self._timer.start()
        cfg = load_app_config()
        cfg["poll_interval_seconds"] = secs
        try:
            save_app_config(cfg)
        except OSError as e:
            rumps.alert("Could not save settings", str(e))
        self._tick(None)

    def _prompt_custom_interval(self, _) -> None:
        win = rumps.Window(
            message="Examples: 90s, 3m, 1h, 1h30m",
            title="Custom refresh interval",
            default_text=format_interval(self._poll_interval),
            ok="Save",
            cancel="Cancel",
            dimensions=(220, 24),
        )
        response = win.run()
        if not response.clicked:
            return
        try:
            secs = parse_interval(response.text)
        except ValueError as e:
            rumps.alert("Invalid interval", str(e))
            return
        if secs < 10:
            rumps.alert("Too short", "Minimum 10s to avoid GitHub rate limits.")
            return
        self._set_poll_interval(secs)

    def _prompt_edit_teammates(self, _) -> None:
        current = ", ".join(load_pr_config()["teammates"])
        win = rumps.Window(
            message="Comma-separated GitHub usernames (leave empty to disable Team section)",
            title="Edit teammates",
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(420, 60),
        )
        response = win.run()
        if not response.clicked:
            return
        new = [t.strip() for t in response.text.replace("\n", ",").split(",") if t.strip()]
        try:
            save_teammates(new)
        except OSError as e:
            rumps.alert("Could not save teammates", str(e))
            return
        self._tick(None)

    def _add_section_header(self, label: str) -> None:
        self.menu.add(self._disabled(label))

    def _add_pr_item(self, pr: dict, label: str, indent: str = "    ") -> None:
        url = pr.get("url", "")
        full_label = f"{indent}{label}"
        item = rumps.MenuItem(full_label, callback=self._open_url_handler(url))
        self.menu.add(item)

    def _open_url_handler(self, url: str):
        def handler(_):
            if url:
                webbrowser.open(url)

        return handler

    @staticmethod
    def _disabled(text: str) -> rumps.MenuItem:
        item = rumps.MenuItem(text)
        item.set_callback(None)
        return item

    @staticmethod
    def _ci_dot(pr: dict) -> str:
        return CI_DOTS.get(pr.get("ci_status") or "none", CI_DOTS["none"])

    @classmethod
    def _format_review_item(cls, pr: dict) -> str:
        repo = pr.get("repository", {}).get("name", "?")
        num = pr.get("number")
        title = pr.get("title", "")
        author = pr.get("author", {}).get("login", "?")
        return f"{cls._ci_dot(pr)}  {repo} #{num} — {title}  ·  @{author}"

    @classmethod
    def _format_mine_item(cls, pr: dict) -> str:
        repo = pr.get("repository", {}).get("name", "?")
        num = pr.get("number")
        title = pr.get("title", "")
        return f"{cls._ci_dot(pr)}  {repo} #{num} — {title}"

    @classmethod
    def _format_team_item(cls, pr: dict) -> str:
        repo = pr.get("repository", {}).get("name", "?")
        num = pr.get("number")
        title = pr.get("title", "")
        return f"{cls._ci_dot(pr)}  {repo} #{num} — {title}"

    def _render_error(self, summary: str, detail: str) -> None:
        self.title = "PR ⚠️"
        self.menu.clear()
        self.menu.add(self._disabled(summary))
        if detail:
            for line in detail.strip().splitlines()[:5]:
                self.menu.add(self._disabled(f"  {line}"))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Retry", callback=self._tick))
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    def _notify_changes(self, data: dict) -> None:
        for pr in data["review"]:
            key = self._pr_key(pr)
            if key not in self._seen_review:
                rumps.notification(
                    title="New review request",
                    subtitle=f"{pr['repository']['name']} #{pr['number']}",
                    message=f"{pr['title']} — by @{pr.get('author', {}).get('login', '?')}",
                    data={"url": pr.get("url", "")},
                    sound=False,
                )

        for pr in data["mine"]:
            key = self._pr_key(pr)
            current = {
                r.get("author", "?"): r.get("state", "")
                for r in (pr.get("reviews") or [])
            }
            previous = self._seen_mine_reviews.get(key, {})
            for author, state in current.items():
                if previous.get(author) != state and state in REVIEW_STATE_LABELS:
                    rumps.notification(
                        title=f"@{author} {REVIEW_STATE_LABELS[state]}",
                        subtitle=f"{pr['repository']['name']} #{pr['number']}",
                        message=pr.get("title", ""),
                        data={"url": pr.get("url", "")},
                        sound=False,
                    )

    def _update_seen(self, data: dict) -> None:
        self._seen_review = {self._pr_key(pr) for pr in data["review"]}
        self._seen_mine_reviews = {
            self._pr_key(pr): {
                r.get("author", "?"): r.get("state", "")
                for r in (pr.get("reviews") or [])
            }
            for pr in data["mine"]
        }

    @staticmethod
    def _pr_key(pr: dict) -> tuple[str, int]:
        return pr["repository"]["nameWithOwner"], int(pr["number"])


@rumps.notifications
def _notification_clicked(info):
    url = (info.get("data") or {}).get("url")
    if url:
        webbrowser.open(url)


if __name__ == "__main__":
    if "--check" in sys.argv:
        path = script_path()
        result = subprocess.run(
            [str(path), "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SCRIPT_TIMEOUT_SECONDS,
            env=subprocess_env(),
        )
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            sys.exit(result.returncode)
        json.loads(result.stdout)
        print("OK")
        sys.exit(0)
    PRStatusApp().run()
