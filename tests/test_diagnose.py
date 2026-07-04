"""Diagnostic matrix: shape and reporting."""

from __future__ import annotations

from hermes_claude_code.config import Config
from hermes_claude_code.diagnose import build_cases, format_matrix, run_matrix


def test_cases_change_one_variable_at_a_time():
    cases = build_cases("sonnet")
    names = [c["name"] for c in cases]
    assert names[0] == "baseline (minimal)"
    # Every payload targets the same model so only the probed dimension varies.
    assert {c["payload"]["model"] for c in cases} == {"sonnet"}
    # Full mode appends the two boundary cases at the end.
    full = build_cases("sonnet", full=True)
    assert len(full) == len(cases) + 2
    assert "OVER 200k" in full[-1]["name"]


def test_run_matrix_classifies_and_formats():
    def fake_post(url, payload, timeout):
        # Fail exactly the fat-tools case; everything else passes.
        if any(
            "diag_tool" in (t["function"]["name"])
            and len(t["function"]["description"]) > 5000
            for t in payload.get("tools", [])
        ):
            return 400, {"error": {"message": "You're out of extra usage."}}
        return 200, {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    cfg = Config()
    results = run_matrix(cfg, post_fn=fake_post)
    by_name = {r["name"]: r for r in results}
    assert by_name["baseline (minimal)"]["ok"] is True
    assert by_name["10 fat tools (~30k tokens schemas)"]["ok"] is False
    assert "extra usage" in by_name["10 fat tools (~30k tokens schemas)"]["error"]

    report = format_matrix(results, cfg)
    assert "✗ 10 fat tools" in report
    assert "isolate the trigger" in report
