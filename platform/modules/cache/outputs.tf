output "name" {
  description = "Serverless cache name (used to sign IAM auth tokens)."
  value       = aws_elasticache_serverless_cache.this.name
}

output "arn" {
  description = "Serverless cache ARN (elasticache:Connect resource)."
  value       = aws_elasticache_serverless_cache.this.arn
}

output "endpoint" {
  description = "Primary endpoint address."
  value       = aws_elasticache_serverless_cache.this.endpoint[0].address
}

output "port" {
  description = "Primary endpoint port."
  value       = aws_elasticache_serverless_cache.this.endpoint[0].port
}

output "user_group_id" {
  description = "User group that customer stacks add their users to."
  value       = aws_elasticache_user_group.this.user_group_id
}
