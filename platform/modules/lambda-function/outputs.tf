output "function_name" {
  description = "Function name."
  value       = aws_lambda_function.this.function_name
}

output "function_arn" {
  description = "Unqualified function ARN."
  value       = aws_lambda_function.this.arn
}

output "alias_name" {
  description = "Alias callers use."
  value       = aws_lambda_alias.live.name
}

output "alias_arn" {
  description = "ARN of the live alias."
  value       = aws_lambda_alias.live.arn
}

output "role_name" {
  description = "Execution role name."
  value       = aws_iam_role.this.name
}

output "log_group_name" {
  description = "Log group name."
  value       = aws_cloudwatch_log_group.this.name
}
