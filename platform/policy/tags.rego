# Tag governance (Pillar 6): every taggable resource carries the five cost tags, so
# the cost report can split spend by customer and env.
package main

import rego.v1

required_tags := {"customer", "env", "owner", "cost-center", "managed-by"}

deny contains msg if {
	some rc in managed_changes
	tags := rc.change.after.tags_all
	is_object(tags)
	missing := required_tags - {k | some k, _ in tags}
	count(missing) > 0
	msg := sprintf("%s: missing required tags %v", [rc.address, sort(missing)])
}
