#!/usr/bin/env bash
# Run Terraform for one stack, the same way CI does.
#
#   tools/tf.sh <stack> <terraform command> [args...]
#   tools/tf.sh shared/dev plan
#   tools/tf.sh customers/acme-tv/dev apply
#
# - refuses to run against any account but the one in .showrunner-account (or
#   $AWS_ACCOUNT_ID in CI)
# - one state key per stack (BRIEF section 5); the stack -> dir/args mapping comes from
#   tools/ci/changed_stacks.py, so local runs and CI can't disagree
# - a separate TF_DATA_DIR per stack, so customers sharing platform/envs never
#   re-initialise over each other
# - builds the Lambda zips first (Terraform needs them to create functions)
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
stack=${1:?usage: tools/tf.sh <stack> <command> [args...]}
cmd=${2:?usage: tools/tf.sh <stack> <command> [args...]}
shift 2

expected=${AWS_ACCOUNT_ID:-$(cat "$ROOT/.showrunner-account" 2>/dev/null || true)}
[[ -n "$expected" ]] || { echo "no account pin: create .showrunner-account or set AWS_ACCOUNT_ID" >&2; exit 2; }
actual=$(aws sts get-caller-identity --query Account --output text)
[[ "$actual" == "$expected" ]] || { echo "refusing: credentials are for account $actual, expected $expected" >&2; exit 2; }

spec=$(cd "$ROOT" && python3 -c '
import json, sys
sys.path.insert(0, "tools/ci")
import changed_stacks as cs
s = cs.discover().get(sys.argv[1])
print(json.dumps(s) if s else "")' "$stack")
[[ -n "$spec" ]] || { echo "unknown stack: $stack" >&2; exit 2; }
dir=$(jq -r .dir <<<"$spec")
mapfile -t stack_args < <(jq -r '.args[]' <<<"$spec")

export TF_VAR_account_id=$expected
export TF_DATA_DIR="$ROOT/.terraform-data/${stack//\//_}"
export TF_PLUGIN_CACHE_DIR=${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}
mkdir -p "$TF_PLUGIN_CACHE_DIR"

if [[ "$stack" != bootstrap ]]; then
  (cd "$ROOT" && uv run --quiet python tools/build/package.py >/dev/null)
fi

terraform -chdir="$ROOT/$dir" init -input=false -upgrade=false \
  -backend-config="bucket=showrunner-tfstate-$expected" \
  -backend-config="key=$stack/terraform.tfstate" >/dev/null

# Gitignored per-env values (alert emails) for the shared stack, if present.
var_files=()
if [[ "$stack" == shared/* && -f "$ROOT/$dir/${stack#shared/}.tfvars" ]]; then
  var_files=("-var-file=${stack#shared/}.tfvars")
fi

case "$cmd" in
  plan|apply|destroy|import|refresh|console)
    exec terraform -chdir="$ROOT/$dir" "$cmd" "${stack_args[@]}" "${var_files[@]}" "$@" ;;
  *)
    exec terraform -chdir="$ROOT/$dir" "$cmd" "$@" ;;
esac
