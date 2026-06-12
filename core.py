"""Pure logic for the PR Status app: config IO, intervals, state building,
notification diffing. No AppKit imports — fully unit-testable."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_POLL_INTERVAL = 300
SCRIPT_TIMEOUT_SECONDS = 120

CONFIG_DIR = Path.home() / ".config" / "pr-status"
PR_CONFIG_PATH = CONFIG_DIR / "config"
APP_CONFIG_PATH = CONFIG_DIR / "app.json"
TEAMMATES_HEADER = "[teammates]"

REVIEW_STATE_LABELS = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "requested changes",
    "COMMENTED": "commented",
}


def script_path() -> Path:
    """Locate pr-status script: bundled (RESOURCEPATH), else next to core.py."""
    here = Path(__file__).resolve().parent
    candidates = []
    rp = os.environ.get("RESOURCEPATH")
    if rp:
        candidates.append(Path(rp) / "pr-status")
    candidates += [here / "pr-status", here.parent / "Resources" / "pr-status"]
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


def ui_path() -> Path:
    """Locate ui/ dir: bundled (RESOURCEPATH), else next to core.py."""
    here = Path(__file__).resolve().parent
    candidates = []
    rp = os.environ.get("RESOURCEPATH")
    if rp:
        candidates.append(Path(rp) / "ui")
    candidates += [here / "ui", here.parent / "Resources" / "ui"]
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


def error_state(
    summary: str,
    detail: str,
    *,
    last_refresh: str | None = None,
    poll_interval_label: str | None = None,
) -> dict:
    return {
        "ok": False,
        "error_summary": summary,
        "error_detail": detail,
        "last_refresh": last_refresh,
        "poll_interval_label": poll_interval_label,
    }


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
