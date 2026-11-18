#!/usr/bin/env bash
# Asserts the CI roles' effective permissions (policies + permission boundary) with the
# IAM policy simulator. Read-only and free. Run after any change to bootstrap IAM:
#   platform/bootstrap/tests/iam-guardrails.sh
set -euo pipefail
cd "$(dirname "$0")/../../.."
A=$(cat .showrunner-account)
[[ "$(aws sts get-caller-identity --query Account --output text)" == "$A" ]] || { echo "wrong AWS account" >&2; exit 2; }
R="arn:aws:iam::${A}:role"; B="arn:aws:s3:::showrunner-tfstate-${A}"
fail=0
check() { # role action resource expected [context]
  local got
  got=$(aws iam simulate-principal-policy --policy-source-arn "$R/$1" --action-names "$2" \
        --resource-arns "$3" ${5:+--context-entries "$5"} \
        --query 'EvaluationResults[0].EvalDecision' --output text)
  if [[ "$got" == "$4" || ( "$4" == "denied" && "$got" == *Deny ) ]]; then s=PASS; else s=FAIL; fail=1; fi
  printf "%s  %-27s %-27s %-45s %s\n" "$s" "$1" "$2" "${3##*[:/]}" "$got"
}
pb() { echo "ContextKeyName=iam:PermissionsBoundary,ContextKeyValues=$1,ContextKeyType=string"; }
it() { echo "ContextKeyName=ec2:InstanceType,ContextKeyValues=$1,ContextKeyType=string"; }
env() { echo "ContextKeyName=aws:ResourceTag/env,ContextKeyValues=$1,ContextKeyType=string"; }
via() { echo "ContextKeyName=kms:ViaService,ContextKeyValues=$1,ContextKeyType=string"; }
lk() { echo "ContextKeyName=dynamodb:LeadingKeys,ContextKeyValues=$1,ContextKeyType=stringList"; }
P="arn:aws:ssm:us-east-1:${A}:parameter"; K="arn:aws:kms:us-east-1:${A}:key/any"

check showrunner-ci-plan s3:GetObject "$B/bootstrap/terraform.tfstate" allowed
check showrunner-ci-plan s3:PutObject "$B/bootstrap/terraform.tfstate" denied
check showrunner-ci-plan s3:PutObject "$B/shared/dev/terraform.tfstate.tflock" allowed
check showrunner-ci-plan ssm:GetParameter "arn:aws:ssm:us-east-1:${A}:parameter/showrunner/secret/x" denied
check showrunner-ci-plan ec2:CreateVpc "*" denied
# ADR 0011: decrypt only via Lambda (function env vars), never via SSM or directly.
# Via Lambda the result is implicitDeny, not explicitDeny: our deny no longer applies and
# the grant comes from the aws/lambda key policy, which the simulator doesn't evaluate.
check showrunner-ci-plan kms:Decrypt "$K" implicitDeny "$(via lambda.us-east-1.amazonaws.com)"
check showrunner-ci-plan kms:Decrypt "$K" denied "$(via ssm.us-east-1.amazonaws.com)"
check showrunner-ci-plan kms:Decrypt "$K" denied
check showrunner-ci-plan secretsmanager:GetSecretValue "arn:aws:secretsmanager:us-east-1:${A}:secret:x" denied
check showrunner-ci-apply-dev s3:PutObject "$B/shared/dev/terraform.tfstate" allowed
check showrunner-ci-apply-dev s3:PutObject "$B/shared/prod/terraform.tfstate" denied
check showrunner-ci-apply-dev s3:PutObject "$B/bootstrap/terraform.tfstate" denied
check showrunner-ci-apply-dev ec2:CreateNatGateway "*" denied
check showrunner-ci-apply-dev eks:CreateCluster "*" denied
check showrunner-ci-apply-dev kms:CreateKey "*" denied
check showrunner-ci-apply-dev secretsmanager:CreateSecret "*" denied
check showrunner-ci-apply-dev iam:CreateUser "arn:aws:iam::${A}:user/x" denied
check showrunner-ci-apply-dev cloudtrail:StopLogging "arn:aws:cloudtrail:us-east-1:${A}:trail/showrunner-trail" denied
check showrunner-ci-apply-dev iam:CreateRole "$R/sr-ok" allowed "$(pb "arn:aws:iam::${A}:policy/sr-workload-boundary")"
check showrunner-ci-apply-dev iam:CreateRole "$R/sr-escape" denied "$(pb arn:aws:iam::aws:policy/AdministratorAccess)"
check showrunner-ci-apply-dev iam:PutRolePolicy "$R/showrunner-ci-apply-prod" denied
check showrunner-ci-apply-prod ec2:RunInstances "arn:aws:ec2:us-east-1:${A}:instance/big" denied "$(it m7i-flex.large)"
check showrunner-ci-apply-prod ec2:RunInstances "arn:aws:ec2:us-east-1:${A}:instance/nat" allowed "$(it t4g.micro)"
check showrunner-ci-apply-prod s3:PutObject "$B/ops-core/terraform.tfstate" allowed
# ADR 0010: ops-core may read exactly two dev outputs, and do nothing else to them
check showrunner-ci-apply-prod ssm:GetParameter "$P/showrunner/dev/shared/alerts_topic_arn" allowed "$(env dev)"
check showrunner-ci-apply-prod ssm:GetParameter "$P/showrunner/dev/shared/appconfig_strategy_id" allowed "$(env dev)"
check showrunner-ci-apply-prod ssm:PutParameter "$P/showrunner/dev/shared/alerts_topic_arn" denied "$(env dev)"
check showrunner-ci-apply-prod ssm:GetParameter "$P/showrunner/dev/shared/vpc_id" denied "$(env dev)"
check showrunner-ci-apply-dev ssm:GetParameter "$P/showrunner/prod/shared/alerts_topic_arn" denied "$(env prod)"
check showrunner-ci-review-agents bedrock:InvokeModel "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0" allowed
check showrunner-ci-review-agents bedrock:InvokeModel "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0" denied
check showrunner-ci-review-agents s3:GetObject "$B/bootstrap/terraform.tfstate" denied
# ADR 0013: the gate may add accept-risk rows (REVIEW#...) to the audit table, nothing else
D="arn:aws:dynamodb:us-east-1:${A}:table"
check showrunner-ci-review-agents dynamodb:PutItem "$D/sr-ops-audit" allowed "$(lk 'REVIEW#pr-12')"
check showrunner-ci-review-agents dynamodb:PutItem "$D/sr-ops-audit" denied "$(lk acme-tv-dev-20261118T1010)"
check showrunner-ci-review-agents dynamodb:PutItem "$D/sr-ops-audit" denied "$(lk 'REVIEW#pr-12,acme-tv-dev-x')"
check showrunner-ci-review-agents dynamodb:PutItem "$D/sr-ops-audit" denied
check showrunner-ci-review-agents dynamodb:UpdateItem "$D/sr-ops-audit" denied "$(lk 'REVIEW#pr-12')"
check showrunner-ci-review-agents dynamodb:DeleteItem "$D/sr-ops-audit" denied "$(lk 'REVIEW#pr-12')"
check showrunner-ci-review-agents dynamodb:PutItem "$D/sr-ops-deploy-log" denied "$(lk 'REVIEW#pr-12')"
check showrunner-ci-review-agents dynamodb:UpdateItem "$D/sr-ops-agent-budget" allowed
check showrunner-ci-deploy-dev lambda:UpdateFunctionCode "arn:aws:lambda:us-east-1:${A}:function:sr-acme-tv-dev-catalog-api" allowed
check showrunner-ci-deploy-dev lambda:UpdateFunctionCode "arn:aws:lambda:us-east-1:${A}:function:sr-acme-tv-prod-catalog-api" denied
check showrunner-ci-deploy-dev ec2:CreateVpc "*" denied
check showrunner-ci-approver states:SendTaskSuccess "*" allowed
check showrunner-ci-approver states:StartExecution "arn:aws:states:us-east-1:${A}:stateMachine:sr-ops-incident-loop" denied
exit $fail
