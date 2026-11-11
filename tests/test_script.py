"""End-to-end tests for the pr-status bash script against a stub `gh`.

The stub serves one open PR authored by the user in repo o/r; the PR's
statusCheckRollup comes from a fixture file so CI mapping can be exercised.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "pr-status"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq required")

STUB_GH = r"""#!/usr/bin/env bash
# Minimal gh stand-in. Behaviour is driven by GH_STUB_* env vars.
args="$*"
PR='{"repository":{"name":"r","nameWithOwner":"o/r"},"number":1,"title":"t","updatedAt":"2026-01-01T00:00:00Z","url":"https://example.test/1"}'
case "$args" in
  "api user -q .login") echo me ;;
  "api /user/teams"*)
    [ -n "${GH_STUB_FAIL_TEAMS:-}" ] && { echo "HTTP 502: bad gateway" >&2; exit 1; }
    echo "[]" ;;
  "search prs"*)
    [ -n "${GH_STUB_FAIL_SEARCH:-}" ] && { echo "HTTP 403: API rate limit exceeded" >&2; exit 1; }
    case "$args" in *"--author=ghost"*) echo 'Invalid search query "author:ghost".' >&2; exit 1 ;; esac
    case "$args" in
      *"--jq"*) echo '["o/r"]' ;;
      *"--author=me"*"--state=open"*) echo "[$PR]" ;;
      *) echo "[]" ;;
    esac ;;
  "api repos/o/r --jq .archived") echo false ;;
  "pr view"*"--json reviews"*)
    [ -n "${GH_STUB_FAIL_REVIEWS:-}" ] && { echo "HTTP 500" >&2; exit 1; }
    echo "[]" ;;
  "pr view"*"--json statusCheckRollup"*)
    [ -n "${GH_STUB_FAIL_ROLLUP:-}" ] && { echo "HTTP 500" >&2; exit 1; }
    cat "$GH_STUB_ROLLUP" ;;
  *) echo "stub gh: unhandled: $args" >&2; exit 2 ;;
esac
"""


def run_script(tmp_path, rollup, config="orgs=o\n", **stub_env):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(STUB_GH)
    gh.chmod(0o755)
    home = tmp_path / "home"
    (home / ".config" / "pr-status").mkdir(parents=True)
    (home / ".config" / "pr-status" / "config").write_text(config)
    rollup_file = tmp_path / "rollup.json"
    rollup_file.write_text(json.dumps({"statusCheckRollup": rollup}))
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(home),
        "GH_STUB_ROLLUP": str(rollup_file),
        **stub_env,
    }
    return subprocess.run(
        [str(SCRIPT), "--json"], capture_output=True, text=True, env=env, timeout=60
    )


def ci_status(tmp_path, rollup):
    result = run_script(tmp_path, rollup)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["counts"]["mine"] == 1
    return data["mine"][0]["ci_status"]


CHECK_OK = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}


@pytest.mark.parametrize(
    "rollup, expected",
    [
        ([], "none"),
        ([CHECK_OK], "success"),
        ([CHECK_OK, {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SKIPPED"}], "success"),
        ([CHECK_OK, {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}], "failure"),
        ([CHECK_OK, {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "CANCELLED"}], "failure"),
        ([CHECK_OK, {"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""}], "pending"),
        # Commit statuses (StatusContext) carry only `state`.
        ([CHECK_OK, {"__typename": "StatusContext", "state": "SUCCESS"}], "success"),
        ([CHECK_OK, {"__typename": "StatusContext", "state": "FAILURE"}], "failure"),
        ([CHECK_OK, {"__typename": "StatusContext", "state": "ERROR"}], "failure"),
        ([CHECK_OK, {"__typename": "StatusContext", "state": "PENDING"}], "pending"),
        ([CHECK_OK, {"__typename": "StatusContext", "state": "EXPECTED"}], "pending"),
    ],
)
def test_ci_rollup_mapping(tmp_path, rollup, expected):
    assert ci_status(tmp_path, rollup) == expected


def test_rollup_fetch_failure_degrades_to_none(tmp_path):
    result = run_script(tmp_path, [CHECK_OK], GH_STUB_FAIL_ROLLUP="1")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mine"][0]["ci_status"] == "none"


@pytest.mark.parametrize(
    "flag, needle",
    [
        ("GH_STUB_FAIL_SEARCH", "rate limit"),
        ("GH_STUB_FAIL_TEAMS", "bad gateway"),
        ("GH_STUB_FAIL_REVIEWS", "HTTP 500"),
    ],
)
def test_github_failure_is_loud_not_empty(tmp_path, flag, needle):
    result = run_script(tmp_path, [CHECK_OK], **{flag: "1"})
    assert result.returncode != 0
    assert needle in result.stderr
    assert result.stdout.strip() == ""


def test_unsearchable_teammate_is_skipped_not_fatal(tmp_path):
    result = run_script(tmp_path, [CHECK_OK], config="orgs=o\n[teammates]\nghost\n")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["counts"] == {"review": 0, "mine": 1, "team": 0}
    assert "ghost" in result.stderr
