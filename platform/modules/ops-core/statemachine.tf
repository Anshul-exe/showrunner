# The loop as a Step Functions Standard workflow: every execution's history is kept for
# 90 days at no extra cost, which is part of the audit story (who, what, when).

locals {
  fn_arn = { for k, m in module.fn : k => m.alias_arn }
  retry = [{
    ErrorEquals     = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "Lambda.TooManyRequestsException"]
    IntervalSeconds = 2, MaxAttempts = 4, BackoffRate = 2
  }]

  invoke = "arn:aws:states:::lambda:invoke"

  definition = {
    Comment = "Showrunner incident loop: agents propose, a human approves (ADR 0005)."
    StartAt = "Open"
    States = {
      Open = {
        Type       = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Route"
        Parameters = { FunctionName = local.fn_arn.github, Payload = { step = "open", "event.$" = "$" } }
      }
      Route = {
        Type = "Choice", Default = "Evidence"
        Choices = [
          { Variable = "$.incident.skip", IsPresent = true, Next = "Done" },
          { Variable = "$.incident.reused", BooleanEquals = true, Next = "Done" },
        ]
      }
      Evidence = {
        Type       = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Triage"
        Parameters = { FunctionName = local.fn_arn.evidence, Payload = { step = "collect", "state.$" = "$" } }
      }
      Triage = {
        Type       = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Verify"
        Parameters = { FunctionName = local.fn_arn.triage, Payload = { "state.$" = "$" } }
      }
      Verify = {
        Type       = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "HasProposal"
        Parameters = { FunctionName = local.fn_arn.verify, Payload = { "state.$" = "$" } }
      }
      HasProposal = {
        Type    = "Choice", Default = "PostAnalysis"
        Choices = [{ Variable = "$.proposal", IsNull = false, Next = "Propose" }]
      }
      PostAnalysis = {
        Type       = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Done"
        Parameters = { FunctionName = local.fn_arn.github, Payload = { step = "analysis", "state.$" = "$" } }
      }
      # Posts the analysis + proposal with the task token, then waits for approve.yml.
      Propose = {
        Type           = "Task", Resource = "${local.invoke}.waitForTaskToken", ResultPath = "$.approval", Next = "Decision"
        TimeoutSeconds = var.approval_timeout_minutes * 60
        Parameters = {
          FunctionName = local.fn_arn.github
          Payload      = { step = "propose", "state.$" = "$", "taskToken.$" = "$$.Task.Token" }
        }
        Catch = [{ ErrorEquals = ["States.Timeout"], ResultPath = "$.error", Next = "EscalateNoApproval" }]
      }
      Decision = {
        Type    = "Choice", Default = "NoteRejected"
        Choices = [{ Variable = "$.approval.decision", StringEquals = "approve", Next = "Act" }]
      }
      NoteRejected = {
        Type = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Done"
        Parameters = {
          FunctionName = local.fn_arn.github
          Payload = {
            step     = "note", "state.$" = "$"
            "text.$" = "States.Format('**Rejected** by {}: {} Nothing was changed; the issue stays open for a human.', $.approval.approver, $.approval.reason)"
          }
        }
      }
      Act = {
        Type       = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "AnnounceAct"
        Parameters = { FunctionName = local.fn_arn.act, Payload = { "state.$" = "$" } }
        Catch      = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "EscalateActFailed" }]
      }
      AnnounceAct = {
        Type = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "WaitCheck"
        Parameters = {
          FunctionName = local.fn_arn.github
          Payload = {
            step     = "note", "state.$" = "$"
            "text.$" = "States.Format('**{} executed**, approved by {}: AppConfig deployment #{} live in {} s, no code deploy. Checking the API p95 every minute until it is back under the threshold.', $.proposal.action_id, $.approval.approver, $.act.deployment, $.act.seconds)"
          }
        }
      }
      WaitCheck = { Type = "Wait", Seconds = 60, Next = "Check" }
      Check = {
        Type       = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Recovered"
        Parameters = { FunctionName = local.fn_arn.evidence, Payload = { step = "check", "state.$" = "$" } }
      }
      Recovered = {
        Type = "Choice", Default = "WaitCheck"
        Choices = [
          { Variable = "$.check.recovered", BooleanEquals = true, Next = "Review" },
          { Variable = "$.check.attempts", NumericGreaterThanEquals = 10, Next = "EscalateNotRecovered" },
        ]
      }
      Review = {
        Type       = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Done"
        Parameters = { FunctionName = local.fn_arn.github, Payload = { step = "pir", "state.$" = "$" } }
      }
      EscalateNoApproval = {
        Type = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Done"
        Parameters = { FunctionName = local.fn_arn.github, Payload = {
        step = "escalate", "state.$" = "$", reason = "No approval within ${var.approval_timeout_minutes} minutes." } }
      }
      EscalateActFailed = {
        Type = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Done"
        Parameters = { FunctionName = local.fn_arn.github, Payload = {
        step = "escalate", "state.$" = "$", reason = "The approved action failed to execute (see the execution history)." } }
      }
      EscalateNotRecovered = {
        Type = "Task", Resource = local.invoke, Retry = local.retry, OutputPath = "$.Payload", Next = "Done"
        Parameters = { FunctionName = local.fn_arn.github, Payload = {
        step = "escalate", "state.$" = "$", reason = "The API p95 did not recover within 10 minutes of the action." } }
      }
      Done = { Type = "Succeed" }
    }
  }
}

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name                 = "${local.name}-incident-loop"
  description          = "Step Functions: invoke the incident loop's five functions, nothing else."
  assume_role_policy   = data.aws_iam_policy_document.sfn_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "sfn" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = flatten([for k, m in module.fn : [m.alias_arn, m.function_arn]])
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${local.name}-incident-loop"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_sfn_state_machine" "loop" {
  #checkov:skip=CKV_AWS_284:X-Ray tracing costs extra; the execution history is the trace (tier 2 adds X-Ray)
  #checkov:skip=CKV_AWS_285:Standard workflows keep full execution history for 90 days without a log group
  name       = "${local.name}-incident-loop"
  type       = "STANDARD"
  role_arn   = aws_iam_role.sfn.arn
  definition = jsonencode(local.definition)
}

# ------------------------------------------------------------------ trigger
resource "aws_cloudwatch_event_rule" "alarm" {
  name        = "${local.name}-alarm-to-incident"
  description = "Any Showrunner alarm entering ALARM starts the incident loop."
  event_pattern = jsonencode({
    source        = ["aws.cloudwatch"]
    "detail-type" = ["CloudWatch Alarm State Change"]
    detail = {
      alarmName = [{ prefix = "sr-" }]
      state     = { value = ["ALARM"] }
    }
  })
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role" "events" {
  name                 = "${local.name}-alarm-to-incident"
  description          = "EventBridge: start the incident loop, nothing else."
  assume_role_policy   = data.aws_iam_policy_document.events_assume.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy" "events" {
  name = "${local.name}-alarm-to-incident"
  role = aws_iam_role.events.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "states:StartExecution", Resource = aws_sfn_state_machine.loop.arn }]
  })
}

resource "aws_cloudwatch_event_target" "loop" {
  rule     = aws_cloudwatch_event_rule.alarm.name
  arn      = aws_sfn_state_machine.loop.arn
  role_arn = aws_iam_role.events.arn
}
