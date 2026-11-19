# The NAT instance (ADR 0003): ~$10/month instead of a NAT gateway's ~$33/month per AZ.
#
#   - Amazon Linux 2023 arm64, t4g.micro, AMI from AWS's public SSM parameter (no
#     community AMIs)
#   - an Auto Scaling group of exactly 1 across both public subnets: if the instance
#     or its AZ dies, the ASG launches another one
#   - user data: IP forwarding + nftables masquerade, source/dest check off, then it
#     points the private route table's 0.0.0.0/0 at itself (self-healing route)
#   - IMDSv2 only, no key pair, no inbound SSH: shell access is SSM Session Manager
#
# Failure mode: while a replacement boots (~2-3 min) the private subnets have no
# internet (vendor calls, AppConfig polls fail; DynamoDB still works via its gateway
# endpoint). That's scenario S5. In production: one NAT gateway per AZ.

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

resource "aws_security_group" "nat" {
  #checkov:skip=CKV_AWS_382:A NAT instance forwards the private subnets' traffic to the internet
  #checkov:skip=CKV_AWS_24:Ingress is the private subnets only; checkov can't evaluate cidrsubnet()
  #checkov:skip=CKV_AWS_25:Ingress is the private subnets only; checkov can't evaluate cidrsubnet()
  #checkov:skip=CKV_AWS_260:Ingress is the private subnets only; checkov can't evaluate cidrsubnet()
  #checkov:skip=CKV_AWS_277:Ingress is the private subnets only; checkov can't evaluate cidrsubnet()
  name        = "${var.name}-nat"
  description = "NAT instance: forwards traffic from the private subnets only"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "Anything from the private subnets (it is their router)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = local.private_cidrs
  }

  egress {
    description = "To the internet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name}-nat" }
}

data "aws_iam_policy_document" "nat_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "nat" {
  name                 = "${var.name}-nat"
  description          = "NAT instance: repoint the private route, disable source/dest check, SSM."
  assume_role_policy   = data.aws_iam_policy_document.nat_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy_attachment" "nat_ssm" {
  role       = aws_iam_role.nat.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "nat" {
  statement {
    sid       = "OwnRouteOnly"
    actions   = ["ec2:ReplaceRoute", "ec2:CreateRoute"]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:route-table/${aws_route_table.private.id}"]
  }

  statement {
    sid       = "SourceDestCheckOnNatInstancesOnly"
    actions   = ["ec2:ModifyInstanceAttribute"]
    resources = ["arn:${data.aws_partition.current.partition}:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/sr-role"
      values   = ["nat-${var.name}"]
    }
  }
}

resource "aws_iam_role_policy" "nat" {
  name   = "${var.name}-nat"
  role   = aws_iam_role.nat.id
  policy = data.aws_iam_policy_document.nat.json
}

resource "aws_iam_instance_profile" "nat" {
  name = "${var.name}-nat"
  role = aws_iam_role.nat.name
}

resource "aws_launch_template" "nat" {
  #checkov:skip=CKV_AWS_88:A NAT instance needs a public IP; inbound is closed (SG) and there is no key pair
  name_prefix = "${var.name}-nat-"
  description = "NAT instance for ${var.name}"
  # Pinned (ADR 0003). Marked sensitive only because the SSM value it replaced was: a
  # change of sensitivity alone is a plan diff, and a launch template diff can cut a new
  # version, which would replace the instance. The ID isn't secret.
  image_id      = sensitive(var.nat_ami_id)
  instance_type = var.nat_instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.nat.arn
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1
  }

  network_interfaces {
    associate_public_ip_address = true
    delete_on_termination       = true
    security_groups             = [aws_security_group.nat.id]
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 8
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  monitoring {
    enabled = false # detailed monitoring costs extra; 5-minute metrics are enough
  }

  user_data = base64encode(templatefile("${path.module}/nat-user-data.sh.tftpl", {
    region         = var.region
    route_table_id = aws_route_table.private.id
    vpc_cidr       = var.cidr
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.name}-nat", sr-role = "nat-${var.name}" }
  }

  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "${var.name}-nat" }
  }

  depends_on = [aws_iam_role_policy.nat, aws_iam_role_policy_attachment.nat_ssm]
}

resource "aws_autoscaling_group" "nat" {
  name                      = "${var.name}-nat"
  min_size                  = 1
  max_size                  = 1
  desired_capacity          = 1
  vpc_zone_identifier       = aws_subnet.public[*].id
  health_check_type         = "EC2"
  health_check_grace_period = 60

  launch_template {
    id      = aws_launch_template.nat.id
    version = aws_launch_template.nat.latest_version
  }

  # A new launch template version (new AMI, new user data) replaces the instance.
  # Brief outage while it boots: acceptable in dev, documented as S5.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0
    }
  }

  tag {
    key                 = "sr-role"
    value               = "nat-${var.name}"
    propagate_at_launch = true
  }
}
