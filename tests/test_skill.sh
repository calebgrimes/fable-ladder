#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
skill="$repo_root/skill/orchestrate"
fail=0
ok()  { printf 'ok    %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1"; fail=1; }

for f in "$repo_root/install.sh" "$skill/scripts/ask_opus.sh" "$repo_root/tests/test_skill.sh"; do
  bash -n "$f" && ok "syntax $(basename "$f")" || bad "syntax $(basename "$f")"
done
[[ -x "$skill/scripts/ask_opus.sh" ]] && ok "helper executable" || bad "helper executable"

head -1 "$skill/SKILL.md" | grep -qx -- '---' && grep -q '^name: orchestrate$' "$skill/SKILL.md" \
  && grep -q '^description: ' "$skill/SKILL.md" && ok "frontmatter" || bad "frontmatter"

for s in 'claude-opus-5-5' 'Sonnet 5.5' 'Haiku 4.5' 'Opus 5.5 speaks:' 'Linear by default' 'Worker report format'; do
  grep -q -- "$s" "$skill/SKILL.md" && ok "SKILL.md has '$s'" || bad "SKILL.md missing '$s'"
done
for s in 'sonnet (Sonnet 5.5)' 'haiku (Haiku 4.5)' 'claude-opus-5-5'; do
  grep -q -- "$s" "$skill/scripts/ask_opus.sh" && ok "helper has '$s'" || bad "helper missing '$s'"
done

# Routing must not leak models from the upstream Codex version.
if grep -rIiqE 'gpt|deepseek|codex|opencode' "$skill"; then bad "foreign model names in skill"; else ok "no foreign model names"; fi
# House style: no em dashes anywhere in the repo.
if grep -rIq $'\xe2\x80\x94' --exclude-dir=.git "$repo_root"; then bad "em dash found"; else ok "no em dashes"; fi
# No credential-shaped strings or personal absolute paths.
if grep -rIqE 'sk-ant-|ghp_|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|/Users/[a-z]' --exclude-dir=.git --exclude=test_skill.sh "$repo_root"; then
  bad "credential or personal path found"; else ok "no credentials or personal paths"; fi

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
"$repo_root/install.sh" --dry-run --target "$tmp/skills" >/dev/null && [[ ! -e "$tmp/skills" ]] && ok "dry run writes nothing" || bad "dry run"
"$repo_root/install.sh" --copy --target "$tmp/skills" >/dev/null && "$repo_root/install.sh" --copy --target "$tmp/skills" >/dev/null \
  && diff -rq "$skill" "$tmp/skills/orchestrate" >/dev/null && ok "idempotent copy matches source" || bad "copy"
rc=0; printf '' | "$skill/scripts/ask_opus.sh" >/dev/null 2>&1 || rc=$?; [[ $rc -eq 64 ]] && ok "empty packet refused" || bad "empty packet"

exit $fail
