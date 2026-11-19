output "vpc_id" {
  description = "VPC ID."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "VPC CIDR."
  value       = aws_vpc.this.cidr_block
}

output "private_subnet_ids" {
  description = "Private subnets (Lambda ENIs, Valkey)."
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "Public subnets (NAT instance)."
  value       = aws_subnet.public[*].id
}

output "private_route_table_id" {
  description = "Route table whose default route the NAT instance owns."
  value       = aws_route_table.private.id
}

output "lambda_security_group_id" {
  description = "Security group for catalog-api functions."
  value       = aws_security_group.lambda.id
}

output "nat_asg_name" {
  description = "The NAT instance's Auto Scaling group."
  value       = aws_autoscaling_group.nat.name
}

output "flow_log_group" {
  description = "VPC flow logs log group."
  value       = aws_cloudwatch_log_group.flow.name
}

output "resolver_log_group" {
  description = "Route 53 Resolver query logs log group."
  value       = aws_cloudwatch_log_group.resolver.name
}
