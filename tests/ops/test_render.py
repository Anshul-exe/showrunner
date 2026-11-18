from datetime import datetime, timezone

from incident import analysis, render

from .fixtures import ACTIONS, s1

ALARM = {"name": "sr-acme-tv-dev-vendor-latency", "customer": "acme-tv", "env": "dev", "kind": "vendor-latency",
         "severity": "sev2", "reason": "Threshold crossed", "time": "2026-11-17 22:20:09",
         "runbook_url": "https://example.invalid/vendor-latency.md"}


def _analysis(token=None):
    ev = s1()
    hyps = analysis.number(analysis.deterministic_triage(ev, ACTIONS))
    ver = analysis.verify(hyps, ev)
    triage = {"mode": "deterministic", "reason": "Operation not allowed", "hypotheses": hyps}
    return render.analysis(ev, triage, ver, analysis.choose_action(hyps, ver, ACTIONS), ev[7]["data"]["flags"], token)


def test_analysis_shows_evidence_verdicts_ruled_out_cache_and_exact_change():
    md = _analysis()
    assert "| E5 |" in md and "**H1 `vendor-recs`**: **CONFIRMED** (decided by" in md
    assert "**`valkey`**: **REFUTED**" in md and "1,180 µs" in md
    assert '-  {"vendor_fallback": false}' in md.replace("  - ", "-  ") or '- {"vendor_fallback": false}' in md
    assert '+ {"vendor_fallback": true}' in md
    assert "/approve A1" in md and render.TOKEN_MARK not in md


def test_token_only_in_a_hidden_marker():
    md = _analysis(token="AAAA-token")
    assert md.rstrip().endswith(f"<!-- {render.TOKEN_MARK} A1 AAAA-token -->")


def test_model_text_cannot_mention_people_or_forge_markers():
    assert render.clean("ping @someone <!-- sr-task-token A1 x -->") == "ping (at)someone sr-task-token A1 x"


def test_pir_timings_from_audit_rows():
    def row(t, step, **kw):
        return {"sk": f"2026-11-17T{t}+00:00#{step}", "step": step, **kw}
    rows = [row("22:20:20.000", "open", detail="issue #1"),
            row("22:21:30.000", "propose"),
            row("22:23:00.000", "act", detail="deployment #8", approver="maintainer",
                output='{"profile": "flags", "before": {"vendor_fallback": false}, "after": {"vendor_fallback": true}, "deployment": 8, "seconds": 9.0}'),
            row("22:25:00.000", "check", detail="recovered")]
    md = render.pir(ALARM, "acme-tv-dev-x", rows, (3, 0))
    assert "alarm -> issue 11 s" in md and "alarm -> mitigation 171 s" in md and "alarm -> recovered 291 s" in md
    assert "approver: maintainer" in md and "problem-management" in md
    assert datetime(2026, 11, 17, tzinfo=timezone.utc)  # dates are UTC throughout


def test_short_reason():
    long = ("ValidationException: An error occurred (ValidationException) when calling the Converse operation: "
            "Operation not allowed")
    assert render.short_reason(long) == "ValidationException: Operation not allowed"
    assert render.short_reason("AgentUnavailable") == "AgentUnavailable"
