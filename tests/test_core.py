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
    assert state["last_refresh"] is None
    assert state["poll_interval_label"] is None


def test_error_state_keeps_footer_info():
    state = core.error_state(
        "Script timeout", "", last_refresh="12:36:40", poll_interval_label="5m"
    )
    assert state["last_refresh"] == "12:36:40"
    assert state["poll_interval_label"] == "5m"


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


def test_save_and_load_teammates_roundtrip(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("orgs=acme\nmax_age_days=21\n[teammates]\nold\n", encoding="utf-8")
    core.save_teammates(["alice", "bob"], path=cfg)
    parsed = core.load_pr_config(cfg)
    assert parsed["teammates"] == ["alice", "bob"]
    assert "orgs=acme" in parsed["top"]


def test_ui_path_falls_back_to_local(monkeypatch, tmp_path):
    fake_core = tmp_path / "core.py"
    monkeypatch.setattr(core, "__file__", str(fake_core))
    assert core.ui_path() == tmp_path / "ui"


def test_ui_path_prefers_existing_dir(monkeypatch, tmp_path):
    (tmp_path / "ui").mkdir()
    monkeypatch.setattr(core, "__file__", str(tmp_path / "core.py"))
    assert core.ui_path() == tmp_path / "ui"


def test_ui_path_uses_resourcepath(monkeypatch, tmp_path):
    (tmp_path / "ui").mkdir()
    monkeypatch.setenv("RESOURCEPATH", str(tmp_path))
    assert core.ui_path() == tmp_path / "ui"


def test_script_path_uses_resourcepath(monkeypatch, tmp_path):
    (tmp_path / "pr-status").write_text("#!/bin/bash\n")
    monkeypatch.setenv("RESOURCEPATH", str(tmp_path))
    assert core.script_path() == tmp_path / "pr-status"


def test_subprocess_env_strips_py2app_openssl_vars(monkeypatch):
    """py2app's __boot__ points SSL_CERT_FILE/DIR at a nonexistent file so the
    bundled Python won't use stale CAs. Inherited by gh (Go >=1.25 honors them
    on darwin) it empties the root pool -> 'certificate signed by unknown
    authority'. They must not reach child processes."""
    monkeypatch.setenv("SSL_CERT_FILE", "/Resources/openssl.ca/no-such-file")
    monkeypatch.setenv("SSL_CERT_DIR", "/Resources/openssl.ca/no-such-file")
    env = core.subprocess_env()
    assert "SSL_CERT_FILE" not in env
    assert "SSL_CERT_DIR" not in env


def test_subprocess_env_keeps_real_openssl_overrides(monkeypatch, tmp_path):
    """A CA bundle the user really set (corporate MITM root) must survive."""
    bundle = tmp_path / "corp-ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    assert core.subprocess_env()["SSL_CERT_FILE"] == str(bundle)
