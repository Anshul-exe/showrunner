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

plan(resources) := {"planned_values": {"root_module": {"child_modules": [{"resources": resources}]}}}

ready := [fn, vendor_latency, dep_errors(["valkey", "dynamodb", "vendor-recs"]), api_latency]

readiness_denies(p) := {m | some m in deny with input as p; startswith(m, "readiness:")}

test_a_ready_customer_passes if {
	count(readiness_denies(plan(ready))) == 0
}

test_missing_dependency_error_alarm if {
	ds := readiness_denies(plan([fn, vendor_latency, dep_errors(["valkey", "vendor-recs"]), api_latency]))
	ds == {"readiness: catalog-api calls dynamodb, but no alarm watches its errors (DependencyErrors)"}
}

test_external_dependency_needs_a_latency_alarm if {
	ds := readiness_denies(plan([fn, dep_errors(["valkey", "dynamodb", "vendor-recs"]), api_latency]))
	ds == {"readiness: catalog-api calls external vendor-recs, but no alarm watches its latency (DependencyLatency)"}
}

test_viewer_latency_alarm_required if {
	ds := readiness_denies(plan([fn, vendor_latency, dep_errors(["valkey", "dynamodb", "vendor-recs"])]))
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
