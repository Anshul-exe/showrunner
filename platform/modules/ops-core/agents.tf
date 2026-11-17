# Agent permissions come from the agents' instruction files (P6: "permissions in
# instruction files"). The file's `iam` list IS the policy: change the file, the plan
# shows the IAM change, and tests/ops/test_agents.py fails CI if an agent is given
# anything but model invocation.

locals {
  agent_files = { for name in ["triage", "verifier"] : name => "${path.module}/../../../ops/agents/${name}.md" }
  agents = { for name, f in local.agent_files :
    name => yamldecode(regex("(?s)^---\\n(.*?)\\n---\\n", file(f))[0])
  }
}

data "aws_iam_policy_document" "agent" {
  for_each                = local.agents
  source_policy_documents = [data.aws_iam_policy_document.audit_write.json]

  dynamic "statement" {
    for_each = each.value.iam
    content {
      sid       = "FromInstructionFile${statement.key}"
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}
