from incident import analysis

from .fixtures import ACTIONS, s1, with_change


def test_s1_triage_points_at_the_vendor_with_its_safe_action():
    hyps = analysis.number(analysis.deterministic_triage(s1(), ACTIONS))
    assert [h["component"] for h in hyps] == ["vendor-recs"]
    assert hyps[0]["action"] == "vendor-fallback-on"
    assert "E5" in hyps[0]["cites"] and "E2" in hyps[0]["cites"]


def test_s1_verifier_confirms_vendor_and_rules_out_the_cache_with_microseconds():
    hyps = analysis.number(analysis.deterministic_triage(s1(), ACTIONS))
    result = analysis.verify(hyps, s1())
    assert result["verdicts"][0]["verdict"] == "confirmed"
    valkey = next(a for a in result["also_checked"] if a["component"] == "valkey")
    assert valkey["verdict"] == "refuted"
    scale = next(c for c in valkey["checks"] if c["name"] == "scale")
    assert "1,180 µs" in scale["detail"] and "E6" in scale["cites"]


def test_a_model_blaming_the_cache_is_refuted_whatever_it_says():
    """The verifier's check is what counts, not the triage model's confidence."""
    hyps = analysis.number([{"component": "valkey", "statement": "The cache is slow.", "cites": ["E2"], "action": None}])
    v = analysis.verify(hyps, s1())["verdicts"][0]
    assert v["verdict"] == "refuted"
    # ...and a model can't flip a conclusive check
    merged = analysis.merge_model_verdicts({"verdicts": [v]}, [{"hypothesis": "H1", "verdict": "confirmed", "explanation": "x"}])
    assert merged["verdicts"][0]["verdict"] == "refuted"


def test_model_decides_only_inconclusive_verdicts():
    result = {"verdicts": [{"hypothesis": "H1", "verdict": "inconclusive", "checks": [], "explanation": "",
                            "decided_by": "checks"}]}
    merged = analysis.merge_model_verdicts(result, [{"hypothesis": "H1", "verdict": "refuted", "explanation": "flat"}])
    assert merged["verdicts"][0] == {**merged["verdicts"][0], "verdict": "refuted", "decided_by": "model", "explanation": "flat"}


def test_a_hypothesis_no_check_can_test_is_not_checkable_and_stays_so():
    """Issue #6: H2 `lambda` and H3 `unknown` came back 'inconclusive (decided by model)'."""
    hyps = analysis.number([{"component": "lambda", "statement": "Cold starts.", "cites": ["E2"], "action": None}])
    result = analysis.verify(hyps, s1())
    assert result["verdicts"][0]["verdict"] == "not-checkable"
    merged = analysis.merge_model_verdicts(result, [{"hypothesis": "H1", "verdict": "confirmed", "explanation": "x"}])
    assert merged["verdicts"][0]["verdict"] == "not-checkable"
    assert analysis.choose_action(hyps, merged, ACTIONS) is None


def test_model_padding_is_dropped():
    """Issue #6's triage output, as the model returned it."""
    ev = s1()
    ids = [e["id"] for e in ev]
    raw = [
        {"component": "vendor-recs", "statement": "Vendor slow.", "cites": ["E1", "E5"], "action": "vendor-fallback-on"},
        {"component": "lambda", "statement": "Cold starts.", "cites": ["E2"], "action": None},
        {"component": "unknown", "statement": "Unknown issue.", "cites": ids[2:], "action": None},
        {"component": "dynamodb", "statement": "Everything.", "cites": ids, "action": None},
        {"component": "vendor-recs", "statement": "Again.", "cites": ["E5"], "action": None},
    ]
    kept = analysis.sanitize_hypotheses(raw, ev, ACTIONS)
    # vendor kept; lambda cites only the API p95 (a symptom); unknown and cite-everything dropped
    assert [h["component"] for h in kept] == ["vendor-recs"]


def test_symptom_only_hypothesis_is_dropped_but_symptom_plus_lead_is_kept():
    ev = s1()
    only_symptom = [{"component": "lambda", "statement": "x", "cites": ["E1", "E2"], "action": None}]
    assert analysis.sanitize_hypotheses(only_symptom, ev, ACTIONS) == []
    with_lead = [{"component": "vendor-recs", "statement": "x", "cites": ["E1", "E2", "E5"], "action": None}]
    assert len(analysis.sanitize_hypotheses(with_lead, ev, ACTIONS)) == 1


def test_action_only_for_confirmed_hypothesis_with_matching_component():
    hyps = analysis.number(analysis.deterministic_triage(s1(), ACTIONS))
    proposal = analysis.choose_action(hyps, analysis.verify(hyps, s1()), ACTIONS)
    assert proposal["id"] == "A1" and proposal["action_id"] == "vendor-fallback-on"
    # vendor not slow -> no proposal
    ev = s1()
    ev[4]["data"].update(now=96.0)
    hyps = analysis.number(analysis.deterministic_triage(ev, ACTIONS))
    assert analysis.choose_action(hyps, analysis.verify(hyps, ev), ACTIONS) is None


def test_config_change_hypothesis_when_something_changed():
    hyps = analysis.deterministic_triage(with_change(), ACTIONS)
    assert [h["component"] for h in hyps] == ["vendor-recs", "config-change"]
    numbered = analysis.number(hyps)
    verdicts = analysis.verify(numbered, with_change())["verdicts"]
    assert verdicts[1]["verdict"] == "confirmed"


def test_no_baseline_is_inconclusive_not_confirmed():
    ev = s1()
    ev[4]["data"].update(baseline=None)
    hyps = analysis.number([{"component": "vendor-recs", "statement": "x", "cites": ["E5"], "action": "vendor-fallback-on"}])
    result = analysis.verify(hyps, ev)
    assert result["verdicts"][0]["verdict"] == "inconclusive"
    assert analysis.choose_action(hyps, result, ACTIONS) is None


def test_sanitize_drops_uncited_and_out_of_scope_output():
    raw = [
        {"component": "vendor-recs", "statement": "ok", "cites": ["E5", "E99"], "action": "vendor-fallback-on"},
        {"component": "valkey", "statement": "uncited", "cites": ["E42"], "action": None},
        {"component": "valkey", "statement": "wrong action", "cites": ["E3"], "action": "vendor-fallback-on"},
        {"component": "kubernetes", "statement": "not ours", "cites": ["E2"], "action": None},
        {"component": "dynamodb", "statement": "made-up action", "cites": ["E4"], "action": "rm -rf"},
    ]
    out = analysis.sanitize_hypotheses(raw, s1(), ACTIONS)
    assert [(h["component"], h["cites"], h["action"]) for h in out] == [
        ("vendor-recs", ["E5"], "vendor-fallback-on"), ("valkey", ["E3"], None), ("dynamodb", ["E4"], None)]


def test_nothing_points_anywhere_gives_unknown():
    ev = s1()
    ev[4]["data"].update(now=96.0)
    hyps = analysis.deterministic_triage(ev, ACTIONS)
    assert [h["component"] for h in hyps] == ["unknown"]


def test_an_old_change_or_one_already_reverted_does_not_confirm():
    # 18/11/2026 run: an hour-old S1 rehearsal (since reset to defaults) was "confirmed".
    # Older than the window: not a hypothesis at all.
    hyps = analysis.deterministic_triage(with_change(minutes_before=41), ACTIONS)
    assert [h["component"] for h in hyps] == ["vendor-recs"]
    # In the window but already reverted: always a hypothesis (G3, 19/11), and refuted.
    ev = with_change(minutes_before=5, at_defaults=True)
    hyps = analysis.number(analysis.deterministic_triage(ev, ACTIONS))
    assert [h["component"] for h in hyps] == ["vendor-recs", "config-change"]
    v = analysis.verify(hyps, ev)["verdicts"][1]
    assert v["verdict"] == "refuted" and "back at their defaults" in v["checks"][0]["detail"]


def test_a_recent_code_deploy_that_is_live_confirms():
    ev = s1()
    ev[8]["data"] = {"versions": [{"version": "4", "at": "22:15:00", "minutes_before_alarm": 5}], "live": "4"}
    hyps = analysis.number(analysis.deterministic_triage(ev, ACTIONS))
    assert [h["component"] for h in hyps] == ["vendor-recs", "config-change"]
    assert analysis.verify(hyps, ev)["verdicts"][1]["verdict"] == "confirmed"
    ev[8]["data"]["live"] = "3"  # published but not live: still a hypothesis, refuted
    hyps = analysis.number(analysis.deterministic_triage(ev, ACTIONS))
    v = analysis.verify(hyps, ev)["verdicts"][1]
    assert v["verdict"] == "refuted" and "isn't live" in v["checks"][0]["detail"]
