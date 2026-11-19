package main

import rego.v1

fn := {"address": "module.service.module.catalog_api.aws_lambda_function.this", "mode": "managed",
	"type": "aws_lambda_function", "values": {"function_name": "sr-acme-tv-dev-catalog-api"}}

alarm(name, values) := {"address": sprintf("module.service.aws_cloudwatch_metric_alarm.%s", [name]),
	"mode": "managed", "type": "aws_cloudwatch_metric_alarm", "values": values}

vendor_latency := alarm("vendor_latency", {
	"alarm_description": "Vendor p95 > 1000 ms. Runbook: https://x/vendor-latency.md",
	"namespace": "Showrunner", "metric_name": "DependencyLatency",
	"dimensions": {"customer": "acme-tv", "env": "dev", "dependency": "vendor-recs"},
	"tags": {"runbook": "vendor-latency"},
})

dep_errors(deps) := alarm("dependency_errors", {
	"alarm_description": "Errors. Runbook: https://x/dependency-errors.md",
	"tags": {"runbook": "dependency-errors"},
	"metric_query": array.concat([{"id": "total", "expression": "a + b", "metric": []}], [q |
		some d in deps
		q := {"id": d, "metric": [{"namespace": "Showrunner", "metric_name": "DependencyErrors",
			"dimensions": {"dependency": d}}]}
	]),
})

api_latency := alarm("api_latency", {
	"alarm_description": "API p95. Runbook: https://x/api-latency.md",
	"namespace": "AWS/ApiGateway", "metric_name": "Latency", "dimensions": {"ApiId": "a"},
	"tags": {"runbook": "api-latency"},
})

canary_alarm := alarm("canary_errors", {
	"alarm_name": "sr-acme-tv-dev-canary-errors",
	"alarm_description": "Errors on live. Runbook: https://x/canary-errors.md",
	"namespace": "AWS/ApiGateway", "metric_name": "5xx", "dimensions": {"ApiId": "a"},
	"tags": {"runbook": "canary-errors"},
})

param(cfg) := {"address": "module.service.aws_ssm_parameter.published[\"canary\"]", "mode": "managed",
	"type": "aws_ssm_parameter", "values": {"name": "/showrunner/dev/customers/acme-tv/canary", "value": json.marshal(cfg)}}

canary := param({"weight": 0.1, "minutes": 5, "alarms": ["sr-acme-tv-dev-canary-errors"]})

plan(resources) := {"planned_values": {"root_module": {"child_modules": [{"resources": resources}]}}}

ready := [fn, vendor_latency, dep_errors(["valkey", "dynamodb", "vendor-recs"]), api_latency, canary_alarm, canary]

readiness_denies(p) := {m | some m in deny with input as p; startswith(m, "readiness:")}

test_a_ready_customer_passes if {
	count(readiness_denies(plan(ready))) == 0
}

test_missing_dependency_error_alarm if {
	ds := readiness_denies(plan([fn, vendor_latency, dep_errors(["valkey", "vendor-recs"]), api_latency, canary_alarm, canary]))
	ds == {"readiness: catalog-api calls dynamodb, but no alarm watches its errors (DependencyErrors)"}
}

test_external_dependency_needs_a_latency_alarm if {
	ds := readiness_denies(plan([fn, dep_errors(["valkey", "dynamodb", "vendor-recs"]), api_latency, canary_alarm, canary]))
	ds == {"readiness: catalog-api calls external vendor-recs, but no alarm watches its latency (DependencyLatency)"}
}

test_viewer_latency_alarm_required if {
	ds := readiness_denies(plan([fn, vendor_latency, dep_errors(["valkey", "dynamodb", "vendor-recs"]), canary_alarm, canary]))
	ds == {"readiness: no alarm on what viewers feel (AWS/ApiGateway Latency)"}
}

test_alarm_with_unknown_runbook if {
	bad := alarm("x", {"alarm_description": "Runbook: https://x/nope.md", "tags": {"runbook": "nope"}})
	ds := readiness_denies(plan(array.concat(ready, [bad])))
	ds == {"readiness: alarm module.service.aws_cloudwatch_metric_alarm.x names runbook \"nope\", which isn't in ops/runbooks"}
}

test_alarm_without_runbook_tag_or_link if {
	bad := alarm("y", {"alarm_description": "no link", "tags": {}})
	count(readiness_denies(plan(array.concat(ready, [bad])))) == 2
}

test_stacks_without_a_service_need_no_service_alarms if {
	count(readiness_denies(plan([]))) == 0
}

# 19/11/2026: latency alarms are metric math (p95 only in minutes with enough calls).
vendor_latency_math := alarm("vendor_latency", {
	"alarm_description": "Vendor p95. Runbook: https://x/vendor-latency.md",
	"tags": {"runbook": "vendor-latency"},
	"metric_query": [
		{"id": "p95_enough", "expression": "IF(calls >= 10, p95, 0)", "metric": []},
		{"id": "p95", "metric": [{"namespace": "Showrunner", "metric_name": "DependencyLatency",
			"dimensions": {"dependency": "vendor-recs"}}]},
	],
})

api_latency_math := alarm("api_latency", {
	"alarm_description": "API p95. Runbook: https://x/api-latency.md",
	"tags": {"runbook": "api-latency"},
	"metric_query": [
		{"id": "p95_enough", "expression": "IF(requests >= 10, p95, 0)", "metric": []},
		{"id": "p95", "metric": [{"namespace": "AWS/ApiGateway", "metric_name": "Latency", "dimensions": {"ApiId": "a"}}]},
	],
})

test_metric_math_latency_alarms_count if {
	count(readiness_denies(plan([fn, vendor_latency_math, dep_errors(["valkey", "dynamodb", "vendor-recs"]), api_latency_math, canary_alarm, canary]))) == 0
}

# 19/11/2026: a canary config is part of readiness (ADR 0016).
no_canary := "readiness: catalog-api has no canary config (a /canary parameter with a weight <= 50% and rollback alarms in this plan)"

almost_ready := [fn, vendor_latency, dep_errors(["valkey", "dynamodb", "vendor-recs"]), api_latency, canary_alarm]

test_a_service_needs_a_canary_config if {
	readiness_denies(plan(almost_ready)) == {no_canary}
}

test_a_canary_of_everything_is_not_a_canary if {
	readiness_denies(plan(array.concat(almost_ready, [param({"weight": 1, "minutes": 5, "alarms": ["sr-acme-tv-dev-canary-errors"]})]))) == {no_canary}
}

test_a_canary_watching_an_alarm_that_is_not_in_the_plan_is_not_one if {
	cfg := {"weight": 0.1, "minutes": 5, "alarms": ["sr-acme-tv-dev-canary-errors", "gone"]}
	readiness_denies(plan(array.concat(almost_ready, [param(cfg)]))) == {no_canary}
}

test_a_canary_without_its_own_error_alarm_is_not_one if {
	cfg := {"weight": 0.1, "minutes": 5, "alarms": ["sr-acme-tv-dev-api-latency"]}
	api := alarm("api_latency", object.union(api_latency.values, {"alarm_name": "sr-acme-tv-dev-api-latency"}))
	readiness_denies(plan([fn, vendor_latency, dep_errors(["valkey", "dynamodb", "vendor-recs"]), api, canary_alarm, param(cfg)])) == {no_canary}
}
