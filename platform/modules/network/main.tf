# One VPC per environment (BRIEF Pillar 2b): two AZs, public subnets for the NAT
# instance, private subnets for Lambda ENIs and Valkey, free gateway endpoints for S3
# and DynamoDB, flow logs. Everything else leaves through the NAT instance (nat.tf).

locals {
  public_cidrs  = [for i, _ in var.azs : cidrsubnet(var.cidr, 8, i)]      # 10.x.0.0/24, 10.x.1.0/24
  private_cidrs = [for i, _ in var.azs : cidrsubnet(var.cidr, 8, 10 + i)] # 10.x.10.0/24, 10.x.11.0/24
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name }
}

# No rules: the default security group denies everything (checkov CKV2_AWS_12).
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-default-deny" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = var.name }
}

resource "aws_subnet" "public" {
  count = length(var.azs)

  vpc_id            = aws_vpc.this.id
  availability_zone = var.azs[count.index]
  cidr_block        = local.public_cidrs[count.index]
  # The NAT instance's launch template asks for a public IP itself; nothing else
  # launched here gets one by accident.
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name}-public-${var.azs[count.index]}", tier = "public" }
}

resource "aws_subnet" "private" {
  count = length(var.azs)

  vpc_id            = aws_vpc.this.id
  availability_zone = var.azs[count.index]
  cidr_block        = local.private_cidrs[count.index]
  tags              = { Name = "${var.name}-private-${var.azs[count.index]}", tier = "private" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-public" }
}

resource "aws_route" "public_default" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# One private route table for both AZs: there is one NAT instance. Its 0.0.0.0/0
# route is NOT managed here: the NAT instance writes it on boot, pointing at itself,
# so a replacement instance heals the route (ADR 0003). Terraform leaves routes it
# doesn't declare alone, so there's no drift.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-private", default-route = "nat-instance" }
}

resource "aws_route_table_association" "private" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Gateway endpoints are free; DynamoDB and S3 traffic never touches the NAT instance.
resource "aws_vpc_endpoint" "gateway" {
  for_each = toset(["s3", "dynamodb"])

  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  tags              = { Name = "${var.name}-${each.key}" }
}

# Shared by every customer's catalog-api in this env. Egress only: it's the source
# the cache security group trusts.
resource "aws_security_group" "lambda" {
  #checkov:skip=CKV_AWS_382:Lambdas call the vendor and AWS APIs on the internet via the NAT instance
  #checkov:skip=CKV2_AWS_5:Attached by the customer stacks' functions (another state)
  name        = "${var.name}-lambda"
  description = "catalog-api functions in ${var.name}"
  vpc_id      = aws_vpc.this.id

  egress {
    description = "Valkey, AWS APIs and the vendor (via NAT)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name}-lambda" }
}

# ------------------------------------------------------------------ flow logs
resource "aws_cloudwatch_log_group" "flow" {
  #checkov:skip=CKV_AWS_158:AWS-managed encryption; customer-managed KMS keys are on the never-create list
  #checkov:skip=CKV_AWS_338:3-day retention on purpose (cost)
  name              = "/sr/${var.name}/vpc-flow-logs"
  retention_in_days = var.flow_log_retention_days
}

data "aws_iam_policy_document" "flow_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow" {
  name                 = "${var.name}-flow-logs"
  assume_role_policy   = data.aws_iam_policy_document.flow_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "flow" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.flow.arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow" {
  name   = "${var.name}-flow-logs"
  role   = aws_iam_role.flow.id
  policy = data.aws_iam_policy_document.flow.json
}

resource "aws_flow_log" "this" {
  vpc_id                   = aws_vpc.this.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow.arn
  iam_role_arn             = aws_iam_role.flow.arn
  max_aggregation_interval = 60
  tags                     = { Name = var.name }
}
