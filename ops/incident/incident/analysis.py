"""Deterministic analysis: the fallback triage, and the verifier's checks.

The checks are code on purpose. "Does the per-dependency latency point there? Is the
scale right? Was there a change in the window?" have exact answers, and an LLM can
misread numbers. The model (when available) ranks and explains; it never overrides a
conclusive check.
"""

from __future__ import annotations

from typing import Any

DEP_COMPONENTS = ("vendor-recs", "valkey", "dynamodb")
COMPONENTS = (*DEP_COMPONENTS, "config-change", "lambda", "network", "unknown")
# The model may not use "unknown" (a catch-all that padded issue #6's list); only the
# deterministic fallback says "unknown", and only as its sole answer ("nothing points").
MODEL_COMPONENTS = tuple(c for c in COMPONENTS if c != "unknown")
MAX_CITES = 4  # a hypothesis citing more is a catch-all ("everything else"), not a lead
CHECKABLE = (*DEP_COMPONENTS, "config-change", "network")
# A runtime flag that points a dependency somewhere else: if it changed and that
# dependency is failing, the change is the cause and its rollback beats a workaround.
FLAG_DEPENDENCY = {"vendor_url": "vendor-recs"}
# Evidence that shows the symptom, not a cause: a hypothesis citing only these has no
# lead of its own (issue #6 re-run: `lambda` citing only the alarm and the API p95).
SYMPTOM_KINDS = ("alarm", "api_latency")
ELEVATED_RATIO = 2.0
ELEVATED_DELTA_MS = 100.0
ERRORS_ELEVATED = 5
SCALE_SHARE = 0.10  # a dependency below 10% of the API's p95 can't explain it
RECENT_CHANGE_MIN = 15  # a change older than this before the alarm can't be its trigger


def by_kind(evidence: list[dict], kind: str) -> list[dict]:
    return [e for e in evidence if e["kind"] == kind]


def dep_item(evidence: list[dict], dep: str) -> dict | None:
    return next((e for e in by_kind(evidence, "dependency_latency") if e["data"]["dependency"] == dep), None)


def api_item(evidence: list[dict]) -> dict | None:
    return next(iter(by_kind(evidence, "api_latency")), None)


def elevated(d: dict) -> bool | None:
    now, base, errs = d.get("now"), d.get("baseline"), d.get("errors_5m", 0) or 0
    if errs >= ERRORS_ELEVATED:
        return True
    if now is None:
        return None
    if base is None:
        return None
    return now >= ELEVATED_RATIO * base and now - base >= ELEVATED_DELTA_MS


def _recent(items: list[dict]) -> list[dict]:
    return [c for c in items if 0 <= (c.get("minutes_before_alarm") if c.get("minutes_before_alarm") is not None
                                      else RECENT_CHANGE_MIN + 1) <= RECENT_CHANGE_MIN]


def recent_changes(evidence: list[dict]) -> dict[str, Any]:
    """Changes that could have triggered this alarm and are still in effect:
    a flags deployment in the 15 min before the alarm while the flags are not at their
    defaults, or the live code version published in those 15 min."""
    ids, config, code, at_defaults = [], [], [], None
    for e in by_kind(evidence, "config_changes"):
        ids.append(e["id"])
        config = _recent(e["data"].get("changes", []))
    for e in by_kind(evidence, "runtime_flags"):
        at_defaults = e["data"].get("at_defaults")
        if config:
            ids.append(e["id"])
    for e in by_kind(evidence, "deploys"):
        ids.append(e["id"])
        live = e["data"].get("live")
        code = [v for v in _recent(e["data"].get("versions", [])) if v.get("version") == live]
    config_in_effect = bool(config) and at_defaults is not True
    return {"found": config_in_effect or bool(code), "config": config, "config_in_effect": config_in_effect,
            "code": code, "at_defaults": at_defaults, "ids": ids}


def changes_in_window(evidence: list[dict]) -> tuple[bool, list[str]]:
    r = recent_changes(evidence)
    return r["found"], r["ids"]


def action_for(component: str, actions: list[dict]) -> str | None:
    return next((a["id"] for a in actions if a.get("applies_to") == component), None)


# ------------------------------------------------------------------ triage fallback
def deterministic_triage(evidence: list[dict], actions: list[dict]) -> list[dict]:
    api = api_item(evidence)
    api_ids = [api["id"]] if api else []
    hyps = []
    for dep in DEP_COMPONENTS:
        item = dep_item(evidence, dep)
        if not item or not elevated(item["data"]):
            continue
        d = item["data"]
        delta = (d["now"] or 0) - (d["baseline"] or 0)
        hyps.append((delta, {
            "component": dep,
            "statement": f"{dep} is the slow or failing dependency: {item['summary']}",
            "impact": "Requests that call it are slow or fail; others are unaffected.",
            "cites": [item["id"], *api_ids],
            "action": action_for(dep, actions),
        }))
    hyps.sort(key=lambda t: -t[0])
    out = [h for _, h in hyps]
    drift = next(iter(by_kind(evidence, "sg_drift")), None)
    if drift and drift["data"].get("drift"):
        cites = [drift["id"], *[e["id"] for k in ("flow_rejects", "reachability", "network_changes")
                                for e in by_kind(evidence, k)]][:4]
        out.insert(0, {
            "component": "network",
            "statement": "The Lambda -> Valkey security-group rule is gone (drift from Terraform): " + drift["summary"],
            "impact": "Every new cache connection fails; requests fall back to DynamoDB, slower.",
            "cites": cites, "action": action_for("network", actions),
        })
    changed, change_ids = changes_in_window(evidence)
    if changed:
        redirected = _flag_points_at_failing_dep(evidence)
        hyp = {
            "component": "config-change",
            "statement": f"A runtime config change or code deploy in the {RECENT_CHANGE_MIN} min before the alarm "
                         "is still in effect.",
            "impact": "Unknown until the change is compared with the symptoms.",
            "cites": change_ids, "action": action_for("config-change", actions),
        }
        if redirected:  # the change points the failing dependency elsewhere: it's the cause
            hyp["statement"] = ("The latest flags deployment changed " + ", ".join(redirected)
                                + ", which points a failing dependency elsewhere; rolling it back fixes the cause.")
            hyp["impact"] = "Every call to that dependency fails until the change is undone."
            out.insert(0, hyp)
        else:
            out.append(hyp)
    if not out:
        out.append({"component": "unknown",
                    "statement": "No dependency's latency or errors point to a cause, and nothing changed in the window.",
                    "impact": "Unknown.", "cites": api_ids or [evidence[0]["id"]], "action": None})
    return out[:3]


def sanitize_hypotheses(raw: list[dict], evidence: list[dict], actions: list[dict]) -> list[dict]:
    """Keep only what the triage agent may propose (its instruction file's may_propose)."""
    ids = {e["id"] for e in evidence}
    allowed = {a["id"]: a for a in actions}
    out = []
    for h in raw:
        if len(out) == 3:
            break
        if not isinstance(h, dict) or h.get("component") not in MODEL_COMPONENTS:
            continue
        if any(o["component"] == h["component"] for o in out):
            continue  # one hypothesis per component
        cites = list(dict.fromkeys(c for c in h.get("cites", []) if c in ids))
        if not cites or len(cites) > MAX_CITES:
            continue  # uncited, or citing everything: dropped, not shown
        kinds = {e["id"]: e.get("kind") for e in evidence}
        if all(kinds.get(c) in SYMPTOM_KINDS for c in cites):
            continue  # only the symptom cited: no evidence points to this component
        action = h.get("action")
        if action not in allowed or allowed[action].get("applies_to") != h["component"]:
            action = None
        out.append({"component": h["component"], "statement": str(h.get("statement", ""))[:400],
                    "impact": str(h.get("impact", ""))[:200], "cites": cites, "action": action})
    return out


def number(hyps: list[dict]) -> list[dict]:
    return [{"id": f"H{i}", **h} for i, h in enumerate(hyps, 1)]


# ------------------------------------------------------------------ verifier checks
def _check(name: str, result: str, detail: str, cites: list[str]) -> dict:
    return {"name": name, "result": result, "detail": detail, "cites": cites}


def check_dependency(dep: str, evidence: list[dict]) -> list[dict]:
    checks = []
    item, api = dep_item(evidence, dep), api_item(evidence)
    if not item:
        return [_check("points-there", "unknown", f"no metrics for {dep}", [])]
    d = item["data"]
    e = elevated(d)
    if e is None:
        checks.append(_check("points-there", "unknown",
                             f"{dep} has no baseline to compare with ({d.get('points', 0)} data points)", [item["id"]]))
    else:
        checks.append(_check("points-there", "pass" if e else "fail",
                             f"{dep} p95 now {d['now']} ms vs baseline {d['baseline']} ms, "
                             f"{d.get('errors_5m', 0):.0f} errors in 5 min", [item["id"]]))
    errs = d.get("errors_5m", 0) or 0
    if errs >= ERRORS_ELEVATED:
        # Failing, not slow: a call that errors fast (a circuit breaker, a refused
        # connection) can't be judged by its share of the API's latency. S3 drill,
        # 18/11/2026: valkey had 8 errors at 1.5 ms and the scale check refuted it.
        checks.append(_check("errors", "pass", f"{dep}: {errs:.0f} errors in 5 min: failing, whatever its latency",
                             [item["id"]]))
        return checks
    if api and api["data"].get("now") and d.get("now") is not None:
        api_now = api["data"]["now"]
        share = d["now"] / api_now if api_now else 0
        if api_now >= 500 and share < SCALE_SHARE:
            detail = f"{dep} p95 {d['now']} ms is {share:.1%} of the API's p95 {api_now:,.0f} ms: it can't explain it"
            cites = [item["id"], api["id"]]
            if dep == "valkey":
                srv = next(iter(by_kind(evidence, "valkey_server_latency")), None)
                if srv and srv["data"].get("read_p99_us"):
                    detail += f"; the cache itself answers in {srv['data']['read_p99_us']:,.0f} µs (server-side p99)"
                    cites.append(srv["id"])
            checks.append(_check("scale", "fail", detail, cites))
        else:
            checks.append(_check("scale", "pass",
                                 f"{dep} p95 {d['now']} ms vs API p95 {api_now:,.0f} ms: same order of magnitude",
                                 [item["id"], api["id"]]))
    return checks


def check_change(evidence: list[dict]) -> list[dict]:
    r = recent_changes(evidence)
    if r["code"]:
        v = r["code"][0]
        detail = f"live code v{v['version']} was published {v['minutes_before_alarm']:.0f} min before the alarm"
    elif r["config_in_effect"]:
        c = r["config"][0]
        detail = (f"flags deployment #{c['deployment']} {c['minutes_before_alarm']:.0f} min before the alarm, "
                  "and the flags are not at their defaults")
    elif r["config"]:
        detail = (f"flags deployment #{r['config'][0]['deployment']} was recent, but the flags are back at their "
                  "defaults, so it can't be causing a problem now")
    else:
        detail = f"no config change or deploy in the {RECENT_CHANGE_MIN} min before the alarm (older ones are listed for context)"
    return [_check("recent-change-in-effect", "pass" if r["found"] else "fail", detail, r["ids"])]


def _flag_points_at_failing_dep(evidence: list[dict]) -> list[str]:
    """Flags (not at default) that redirect a dependency whose errors/latency are up."""
    flags = next(iter(by_kind(evidence, "runtime_flags")), None)
    if not flags:
        return []
    out = []
    for key, dep in FLAG_DEPENDENCY.items():
        value = (flags["data"].get("flags") or {}).get(key)
        item = dep_item(evidence, dep)
        if value and item and elevated(item["data"]):
            out.append(f"`{key}` to `{value}`")
    return out


def _host(url: str) -> str:
    return url.split("://", 1)[-1].split("/", 1)[0].rstrip(".").lower()


def check_dns(evidence: list[dict]) -> list[dict]:
    """Does a host set in the flags fail to resolve from inside the VPC? (S4)"""
    flags = next(iter(by_kind(evidence, "runtime_flags")), None)
    dns = next(iter(by_kind(evidence, "dns_errors")), None)
    if not flags or not dns:
        return []
    out = []
    for key in FLAG_DEPENDENCY:
        value = (flags["data"].get("flags") or {}).get(key)
        if not value:
            continue
        host = _host(value)
        hits = [n for n in dns["data"].get("names", []) if n.get("name", "").rstrip(".").lower() == host]
        if hits:
            out.append(_check("changed-host-resolves", "pass",
                              f"`{key}` points at `{host}`, which doesn't resolve from the VPC: "
                              + ", ".join(f"{h['rcode']} x{h['queries']}" for h in hits), [flags["id"], dns["id"]]))
        else:
            out.append(_check("changed-host-resolves", "unknown",
                              f"`{key}` points at `{host}`; no DNS errors logged for it", [flags["id"], dns["id"]]))
    return out


def check_network(evidence: list[dict]) -> list[dict]:
    drift = next(iter(by_kind(evidence, "sg_drift")), None)
    if not drift:
        return [_check("sg-rule", "unknown", "security-group state not collected", [])]
    d = drift["data"]
    checks = [_check("sg-rule", "pass" if d.get("drift") else "fail",
                     (f"the cache security group is missing port(s) {d.get('missing_ports')} from the Lambda security "
                      "group" if d.get("drift") else "the Lambda -> Valkey rule is in place, as Terraform declares"),
                     [drift["id"]])]
    flows = next(iter(by_kind(evidence, "flow_rejects")), None)
    if flows and (flows["data"].get("surge") or not d.get("drift")):
        f = flows["data"]
        checks.append(_check("flow-rejects", "pass" if f.get("surge") else "fail",
                             f"{f.get('now')} REJECTed flows to 6379/6380 in 10 min vs {f.get('baseline')} in the hour "
                             "before", [flows["id"]]))
    reach = next(iter(by_kind(evidence, "reachability")), None)
    if reach and reach["data"].get("reachable") is not None:
        blocked = not reach["data"]["reachable"]
        checks.append(_check("reachability", "pass" if blocked else "fail",
                             "Reachability Analyzer: catalog-api -> Valkey 6379 " + ("blocked" if blocked else "reachable"),
                             [reach["id"]]))
    return checks


def checks_for(component: str, evidence: list[dict]) -> list[dict]:
    if component in DEP_COMPONENTS:
        return check_dependency(component, evidence)
    if component == "config-change":
        return check_change(evidence) + check_dns(evidence)
    if component == "network":
        return check_network(evidence)
    return [_check("no-deterministic-check", "unknown", f"no automatic check for '{component}'", [])]


def verdict_from(checks: list[dict]) -> str:
    results = [c["result"] for c in checks]
    if [c["name"] for c in checks] == ["no-deterministic-check"]:
        return "not-checkable"  # shown for a human, never decided by the model, never acted on
    if "fail" in results:
        return "refuted"
    if results and all(r == "pass" for r in results):
        return "confirmed"
    return "inconclusive"


def verify(hyps: list[dict], evidence: list[dict]) -> dict[str, Any]:
    """Deterministic verdict per hypothesis, plus the dependencies triage did NOT name
    (the legacy view's suspects): each one is checked and shown as ruled out or not."""
    verdicts = []
    for h in hyps:
        checks = checks_for(h["component"], evidence)
        verdicts.append({"hypothesis": h["id"], "verdict": verdict_from(checks), "checks": checks,
                         "explanation": "", "decided_by": "checks"})
    named = {h["component"] for h in hyps}
    also = []
    for dep in DEP_COMPONENTS:
        if dep in named:
            continue
        checks = checks_for(dep, evidence)
        also.append({"component": dep, "verdict": verdict_from(checks), "checks": checks})
    return {"verdicts": verdicts, "also_checked": also}


def merge_model_verdicts(result: dict[str, Any], model: list[dict]) -> dict[str, Any]:
    """The model explains every verdict; it decides only the inconclusive ones."""
    by_h = {m["hypothesis"]: m for m in model}
    for v in result["verdicts"]:
        m = by_h.get(v["hypothesis"])
        if not m:
            continue
        v["explanation"] = str(m.get("explanation", ""))[:400]
        if v["verdict"] == "inconclusive" and m.get("verdict") in ("confirmed", "refuted", "inconclusive"):  # not not-checkable
            v["verdict"], v["decided_by"] = m["verdict"], "model"
    return result


def choose_action(hyps: list[dict], verification: dict[str, Any], actions: list[dict],
                  evidence: list[dict] | None = None) -> dict | None:
    """A1 = the safe action of the highest-ranked CONFIRMED hypothesis; a confirmed root
    cause (network drift, or a flag that redirects the failing dependency) goes first."""
    verdict = {v["hypothesis"]: v["verdict"] for v in verification["verdicts"]}
    allowed = {a["id"]: a for a in actions}
    redirected = bool(_flag_points_at_failing_dep(evidence or []))

    def root_cause_first(h: dict) -> int:  # undo the cause before working around a symptom
        return 0 if h["component"] == "network" or (h["component"] == "config-change" and redirected) else 1

    for h in sorted(hyps, key=root_cause_first):  # stable: otherwise triage's ranking
        a = allowed.get(h.get("action") or "")
        if verdict.get(h["id"]) == "confirmed" and a and a.get("applies_to") == h["component"]:
            proposal = {"id": "A1", "action_id": a["id"], "hypothesis": h["id"], "spec": a}
            if (a.get("appconfig") or {}).get("rollback"):
                r = recent_changes(evidence or [])
                if not r["config"]:
                    continue  # nothing recent to roll back
                proposal["params"] = {"deployment": r["config"][0]["deployment"]}
            return proposal
    return None
