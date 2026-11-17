resource "aws_ssm_parameter" "out" {
  #checkov:skip=CKV2_AWS_34:Non-secret names and ARNs; SecureString would make every reader need kms:Decrypt
  for_each = {
    state_machine_arn = aws_sfn_state_machine.loop.arn
    audit_table       = aws_dynamodb_table.audit.name
  }
  name  = "/showrunner/ops/incident/${each.key}"
  type  = "String"
  value = each.value
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.loop.arn
}

output "audit_table" {
  value = aws_dynamodb_table.audit.name
}

output "function_names" {
  value = { for k, m in module.fn : k => m.function_name }
}
