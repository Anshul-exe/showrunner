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

check showrunner-ci-plan s3:GetObject "$B/bootstrap/terraform.tfstate" allowed
check showrunner-ci-plan s3:PutObject "$B/bootstrap/terraform.tfstate" denied
check showrunner-ci-plan s3:PutObject "$B/shared/dev/terraform.tfstate.tflock" allowed
check showrunner-ci-plan ssm:GetParameter "arn:aws:ssm:us-east-1:${A}:parameter/showrunner/secret/x" denied
check showrunner-ci-plan ec2:CreateVpc "*" denied
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
check showrunner-ci-review-agents bedrock:InvokeModel "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0" allowed
check showrunner-ci-review-agents bedrock:InvokeModel "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0" denied
check showrunner-ci-review-agents s3:GetObject "$B/bootstrap/terraform.tfstate" denied
check showrunner-ci-deploy-dev lambda:UpdateFunctionCode "arn:aws:lambda:us-east-1:${A}:function:sr-acme-tv-dev-catalog-api" allowed
check showrunner-ci-deploy-dev lambda:UpdateFunctionCode "arn:aws:lambda:us-east-1:${A}:function:sr-acme-tv-prod-catalog-api" denied
check showrunner-ci-deploy-dev ec2:CreateVpc "*" denied
check showrunner-ci-approver states:SendTaskSuccess "*" allowed
check showrunner-ci-approver states:StartExecution "arn:aws:states:us-east-1:${A}:stateMachine:sr-ops-incident-loop" denied
exit $fail
