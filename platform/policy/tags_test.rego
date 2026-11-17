package main

import rego.v1

test_missing_tags_denied if {
	msgs := deny with input as {"resource_changes": [{
		"address": "aws_s3_bucket.site",
		"mode": "managed",
		"type": "aws_s3_bucket",
		"change": {"actions": ["create"], "after": {"tags_all": {"customer": "acme-tv", "env": "dev"}}},
	}]}
	count(msgs) == 1
	some m in msgs
	contains(m, "cost-center")
}

test_all_tags_allowed if {
	count(deny) == 0 with input as change("aws_s3_bucket", {})
}

test_untaggable_resource_skipped if {
	count(deny) == 0 with input as {"resource_changes": [{
		"address": "aws_s3_bucket_policy.site",
		"mode": "managed",
		"type": "aws_s3_bucket_policy",
		"change": {"actions": ["create"], "after": {"bucket": "x"}},
	}]}
}
