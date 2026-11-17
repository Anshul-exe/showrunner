package main

import rego.v1

tags := {"customer": "acme-tv", "env": "dev", "owner": "x", "cost-center": "y", "managed-by": "terraform"}

change(type, after) := {"resource_changes": [{
	"address": sprintf("%s.this", [type]),
	"mode": "managed",
	"type": type,
	"change": {"actions": ["create"], "after": object.union({"tags_all": tags}, after)},
}]}

test_nat_gateway_denied if {
	count(deny) == 1 with input as change("aws_nat_gateway", {})
}

test_eks_denied if {
	count(deny) == 1 with input as change("aws_eks_cluster", {})
}

test_rds_denied if {
	count(deny) == 1 with input as change("aws_db_instance", {})
}

test_kms_key_denied if {
	count(deny) == 1 with input as change("aws_kms_key", {})
}

test_secretsmanager_denied if {
	count(deny) == 1 with input as change("aws_secretsmanager_secret", {})
}

test_standalone_instance_denied if {
	count(deny) == 1 with input as change("aws_instance", {})
}

test_interface_endpoint_denied if {
	count(deny) == 1 with input as change("aws_vpc_endpoint", {"vpc_endpoint_type": "Interface"})
}

test_gateway_endpoint_allowed if {
	count(deny) == 0 with input as change("aws_vpc_endpoint", {"vpc_endpoint_type": "Gateway"})
}

test_nat_launch_template_allowed if {
	count(deny) == 0 with input as change("aws_launch_template", {"instance_type": "t4g.micro"})
}

test_bigger_launch_template_denied if {
	count(deny) == 1 with input as change("aws_launch_template", {"instance_type": "m7i-flex.large"})
}

test_asg_of_two_denied if {
	count(deny) == 1 with input as change("aws_autoscaling_group", {"max_size": 2})
}

test_waf_without_flag_denied if {
	count(deny) == 1 with input as change("aws_wafv2_web_acl", {})
}

test_waf_with_flag_allowed if {
	count(deny) == 0 with input as change("aws_wafv2_web_acl", {"tags_all": object.union(tags, {"guarded-by": "enable_waf"})})
}

test_public_zone_denied if {
	count(deny) == 1 with input as change("aws_route53_zone", {"vpc": []})
}

test_delegated_subdomain_zone_allowed if {
	count(deny) == 0 with input as change("aws_route53_zone", {"vpc": [], "tags_all": object.union(tags, {"purpose": "delegated-subdomain"})})
}

test_multi_region_trail_denied if {
	count(deny) == 1 with input as change("aws_cloudtrail", {"is_multi_region_trail": true})
}

test_deletes_are_not_checked if {
	count(deny) == 0 with input as {"resource_changes": [{
		"address": "aws_nat_gateway.old",
		"mode": "managed",
		"type": "aws_nat_gateway",
		"change": {"actions": ["delete"], "after": null},
	}]}
}

test_lambda_allowed if {
	count(deny) == 0 with input as change("aws_lambda_function", {})
}
