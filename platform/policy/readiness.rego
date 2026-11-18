# Readiness gate (Pillar 1 + P9): a customer environment may not go live without the
# alarms that make an incident diagnosable. Checked on `terraform show -json`, in the
# PR, using the plan's full desired state (planned_values), not just what changed.
#
#   - every alarm names a runbook that exists (tag `runbook`) and links it
#   - a service's every dependency has an error alarm; external ones a latency alarm
#     too ("endpoint-level metrics for every dependency" as a condition of readiness)
#   - viewers' latency (API Gateway p95) is alarmed
#
# `runbooks` must equal the files in ops/runbooks (tests/test_policy_data.py).
package main

import rego.v1

runbooks := {"api-latency", "dependency-errors", "vendor-latency"}

# What each service calls, and which of those are outside our control.
services := {"catalog-api": {
	"dependencies": {"valkey", "dynamodb", "vendor-recs"},
	"external": {"vendor-recs"},
}}

planned_resources contains r if {
	walk(input.planned_values, [_, r])
	is_object(r)
	r.mode == "managed"
	is_string(r.type)
}

alarms contains r if {
	some r in planned_resources
	r.type == "aws_cloudwatch_metric_alarm"
}

deployed_services contains name if {
	some r in planned_resources
	r.type == "aws_lambda_function"
	some name, _ in services
	endswith(r.values.function_name, sprintf("-%s", [name]))
}

# Every (metric_name, dependency) an alarm watches: single-metric alarms and each
# metric inside a metric-math alarm.
watched contains [a.values.metric_name, a.values.dimensions.dependency] if {
	some a in alarms
	a.values.dimensions.dependency
}

watched contains [m.metric_name, m.dimensions.dependency] if {
	some a in alarms
	some q in a.values.metric_query
	some m in q.metric
	m.dimensions.dependency
}

deny contains msg if {
	some a in alarms
	not a.values.tags.runbook
	msg := sprintf("readiness: alarm %s has no `runbook` tag", [a.address])
}

deny contains msg if {
	some a in alarms
	rb := a.values.tags.runbook
	not rb in runbooks
	msg := sprintf("readiness: alarm %s names runbook %q, which isn't in ops/runbooks", [a.address, rb])
}

deny contains msg if {
	some a in alarms
	not contains(object.get(a.values, "alarm_description", ""), "Runbook: ")
	msg := sprintf("readiness: alarm %s doesn't link its runbook in alarm_description", [a.address])
}

deny contains msg if {
	some svc in deployed_services
	some dep in services[svc].dependencies
	not ["DependencyErrors", dep] in watched
	msg := sprintf("readiness: %s calls %s, but no alarm watches its errors (DependencyErrors)", [svc, dep])
}

deny contains msg if {
	some svc in deployed_services
	some dep in services[svc].external
	not ["DependencyLatency", dep] in watched
	msg := sprintf("readiness: %s calls external %s, but no alarm watches its latency (DependencyLatency)", [svc, dep])
}

deny contains msg if {
	count(deployed_services) > 0
	not api_latency_alarm
	msg := "readiness: no alarm on what viewers feel (AWS/ApiGateway Latency)"
}

api_latency_alarm if {
	some a in alarms
	a.values.namespace == "AWS/ApiGateway"
	a.values.metric_name == "Latency"
}
