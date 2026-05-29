#!/usr/bin/env bash
# PreToolUse hook (Bash): block high-risk shell commands before they run.
#
# Exit 2 blocks the call and surfaces the reason to Claude. Patterns are kept
# tight and high-confidence so normal work passes: bare `npm install` (lockfile
# restore), local `npx eslint`, force-push to a FEATURE branch, and `rm -rf`
# under /tmp all go through. When in doubt the hook lets it pass.
#
# Command-name checks are anchored to the START of a command segment (the string
# is split on ; && || | $(), one segment per line) so a pattern merely MENTIONED
# inside a quoted arg, commit message, or heredoc body does NOT trip the guard —
# only an actual command invocation does.
set -uo pipefail

cmd="$(jq -r '.tool_input.command // empty' 2>/dev/null)"
[ -z "$cmd" ] && exit 0

# segments: split on command separators so ^ anchors to a real command start
segs="$(printf '%s' "$cmd" | sed -E 's/\$\(|\|\||&&|[;|&]/\n/g')"
seg() { printf '%s\n' "$segs" | grep -Eq "$1"; }

deny() { echo "BLOCKED by guard-bash hook: $1. If you're sure, run it yourself in a real shell." >&2; exit 2; }

# 1. pipe-to-shell — remote code execution (whole command: the pipe is the signal)
echo "$cmd" | grep -Eq '(curl|wget)\b[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(sh|bash|zsh)\b' \
  && deny "pipe-to-shell (curl|wget … | sh) — runs unverified remote code"

# 2. npx auto-yes — fetches + runs an UNPINNED package from the registry
seg '^[[:space:]]*(sudo[[:space:]]+)?npx[[:space:]]+(-y|--yes)\b' \
  && deny "npx -y runs an unpinned registry package (supply-chain risk). Use the local binary in node_modules/.bin"

# 3. adding a new npm dependency (bare `npm install` / `npm ci` is fine)
seg '^[[:space:]]*(sudo[[:space:]]+)?npm[[:space:]]+(i|install|add)[[:space:]]+@?[a-z0-9][a-z0-9._/-]*' \
  && deny "npm install <pkg> pulls an unpinned dependency — vet it and pin in package.json by hand"

# 4. force-push to a protected branch (feature-branch force-push is allowed)
if seg '^[[:space:]]*(sudo[[:space:]]+)?git[[:space:]]+push\b.*(--force|--force-with-lease|[[:space:]]-f\b)' \
   && seg '^[[:space:]]*(sudo[[:space:]]+)?git[[:space:]]+push\b.*\b(main|develop|origin/main|origin/develop)\b'; then
  deny "force-push to a protected branch (main/develop)"
fi

# 5. catastrophic rm — recursive/force flag against a system or home root
if seg '^[[:space:]]*(sudo[[:space:]]+)?rm\b[^|;&]*(-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR]|--recursive|--force)' \
   && { seg '^[[:space:]]*(sudo[[:space:]]+)?rm\b[^|;&]*[[:space:]](/|/\*|~|~/|\$HOME|\$\{HOME\})([[:space:]]|$)' \
        || seg '^[[:space:]]*(sudo[[:space:]]+)?rm\b[^|;&]*[[:space:]]/(etc|usr|var|bin|boot|lib|sbin|opt|root)([[:space:]/]|$)'; }; then
  deny "rm -rf targeting a system/home root"
fi

# 6. reading secret/key material into the transcript. Best-effort speed bump
#    ONLY — shell is too flexible to catch every form (eval, base64 -d, variable
#    expansion, process substitution, …). The AUTHORITATIVE secret-read gate is
#    block-sensitive.sh on the Read tool (exact-basename match at the file layer).
#    No template allowlist here: the old `! seg '…example…'` exception was a
#    free-floating substring and trivially bypassable (`cat secret.yaml tmpl`),
#    so reading the committed templates via Bash is simply blocked too — harmless.
#    Covers indirect invokers (sh -c / bash -c / eval / xargs) when a secret path
#    appears in the same command segment.
if seg '^[[:space:]]*(sudo[[:space:]]+)?(cat|less|more|head|tail|bat|nl|xxd|od|strings|sh|bash|zsh|eval|xargs)\b[^|]*(helm/secrets/[^[:space:]]*\.ya?ml|([[:space:]/])\.env\b|[^[:space:]]*\.pem\b|id_rsa|id_ed25519|[^[:space:]]*\.kubeconfig)'; then
  deny "reading secret/key material into the transcript (best-effort; block-sensitive.sh on the Read tool is the real gate)"
fi

exit 0
