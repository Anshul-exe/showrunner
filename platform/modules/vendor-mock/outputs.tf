output "url" {
  description = "Public HTTPS base URL of the mock vendor."
  value       = trimsuffix(aws_lambda_function_url.this.function_url, "/")
}

output "function_name" {
  description = "Function name."
  value       = module.function.function_name
}
