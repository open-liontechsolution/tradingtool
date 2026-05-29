#!/usr/bin/env bash
# PreToolUse hook (Read|Edit|Write): refuse to let Claude read OR modify
# deployment secrets / private key material. Blocking reads keeps secrets out of
# the transcript; blocking writes keeps Claude from clobbering them. Aligns with
# the #73/#80 secret-leak incidents.
#
# Exit 2 blocks the tool call and surfaces the stderr message back to Claude.
# Templates/examples are explicitly allowed so scaffolding still works.
set -uo pipefail

file="$(jq -r '.tool_input.file_path // empty' 2>/dev/null)"
[ -z "$file" ] && exit 0
base="$(basename "$file")"

deny() { echo "BLOCKED by block-sensitive hook: $1 ($file). Access it by hand if you really mean to." >&2; exit 2; }

# helm secrets: block every helm/secrets/*.yaml except the committed template.
case "$file" in
  */helm/secrets/*.yaml|*/helm/secrets/*.yml)
    [ "$base" = "example.yaml" ] || deny "helm/secrets/*.yaml holds real deployment secrets (gitignored)" ;;
  */helm/env/secrets*.yaml|*/helm/env/secrets*.yml)
    deny "legacy helm/env secrets file" ;;
esac

# real .env files (templates like .env.example / .env.development are allowed)
case "$base" in
  .env|.env.local|.env.production|.env.production.local)
    deny "real .env secrets — use .env.example for templates" ;;
  *.pem|*.key|id_rsa|id_ed25519|*.kubeconfig)
    deny "private key / kubeconfig material" ;;
esac
exit 0
