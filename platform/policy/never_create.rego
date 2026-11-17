# The never-create list (BRIEF section 3), checked on `terraform show -json` output.
# The CI permission boundary denies most of these at the API too; this rule catches
# them at plan time, in the PR, before anything is applied.
package main

import rego.v1

managed_changes contains rc if {
	some rc in input.resource_changes
	rc.mode == "managed"
	some action in rc.change.actions
	action in {"create", "update"}
}

# Resource types that must never be created, with the reason shown in the PR.
forbidden := {
	"aws_eks_cluster": "EKS control plane is ~$73/month (ADR 0001)",
	"aws_eks_node_group": "no EKS (ADR 0001)",
	"aws_eks_fargate_profile": "no EKS (ADR 0001)",
	"aws_nat_gateway": "NAT gateway is ~$33/month per AZ idle; use the NAT instance (ADR 0003)",
	"aws_db_instance": "no RDS",
	"aws_rds_cluster": "no RDS/Aurora",
	"aws_rds_cluster_instance": "no RDS/Aurora",
	"aws_mq_broker": "no Amazon MQ",
	"aws_opensearch_domain": "no OpenSearch",
	"aws_elasticsearch_domain": "no OpenSearch",
	"aws_opensearchserverless_collection": "no OpenSearch Serverless",
	"aws_lb": "no ALB/NLB: API Gateway + CloudFront front everything",
	"aws_alb": "no ALB/NLB",
	"aws_elb": "no classic ELB",
	"aws_kms_key": "no customer-managed KMS keys ($1/month each); use AWS-managed keys",
	"aws_secretsmanager_secret": "no Secrets Manager ($0.40/secret/month); use SSM SecureString",
	"aws_instance": "the only EC2 instances are the NAT instances, launched by their ASG",
	"aws_spot_instance_request": "no standalone EC2",
	"aws_spot_fleet_request": "no standalone EC2",
	"aws_ec2_fleet": "no standalone EC2",
	"aws_ec2_transit_gateway": "Transit Gateway isn't on the Free plan",
	"aws_elasticache_cluster": "only ElastiCache Serverless for Valkey",
	"aws_elasticache_replication_group": "only ElastiCache Serverless for Valkey",
	"aws_iam_user": "no IAM users from Terraform: CI uses OIDC roles",
	"aws_iam_access_key": "no long-lived access keys (CLAUDE.md rule 2)",
}

deny contains msg if {
	some rc in managed_changes
	reason := forbidden[rc.type]
	msg := sprintf("%s: never-create list: %s", [rc.address, reason])
}

# Gateway endpoints (S3, DynamoDB) are free; interface endpoints are $7+/month per AZ.
deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_vpc_endpoint"
	rc.change.after.vpc_endpoint_type != "Gateway"
	msg := sprintf("%s: interface VPC endpoints are on the never-create list; use a gateway endpoint or the NAT instance", [rc.address])
}

# The NAT instance launch template: the smallest Free-plan arm64 type only.
deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_launch_template"
	rc.change.after.instance_type != "t4g.micro"
	msg := sprintf("%s: instance_type %v is not allowed; NAT instances are t4g.micro", [rc.address, rc.change.after.instance_type])
}

# NAT instance ASGs are "ASG of 1".
deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_autoscaling_group"
	rc.change.after.max_size > 1
	msg := sprintf("%s: max_size %v > 1; the only ASG is the NAT instance (ASG of 1)", [rc.address, rc.change.after.max_size])
}

# WAF exists only behind the enable_waf flag, which tags what it creates.
deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_wafv2_web_acl"
	object.get(object.get(rc.change.after, "tags_all", {}), "guarded-by", "") != "enable_waf"
	msg := sprintf("%s: WAF must be created only behind enable_waf (tag guarded-by=enable_waf)", [rc.address])
}

# The only public hosted zone is the delegated subdomain (phase 5).
deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_route53_zone"
	count(object.get(rc.change.after, "vpc", [])) == 0
	object.get(object.get(rc.change.after, "tags_all", {}), "purpose", "") != "delegated-subdomain"
	msg := sprintf("%s: public hosted zones are limited to the delegated subdomain (tag purpose=delegated-subdomain)", [rc.address])
}

# The Free plan supports no multi-region features.
deny contains msg if {
	some rc in managed_changes
	rc.type == "aws_cloudtrail"
	rc.change.after.is_multi_region_trail == true
	msg := sprintf("%s: multi-region trails aren't supported on the Free plan", [rc.address])
}
