#!/usr/bin/env bash
# Dependency-light checks for the ladder plugin: layout, routing strings, house
# style, then the engine's own validate and test runs. Exit 0 means all passed.
set -uo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
skill="$repo_root/skills/orchestrate"
mod="$repo_root/hooks/ladder.mjs"
fail=0
ok()  { printf 'ok    %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1"; fail=1; }

# Layout
for f in .claude-plugin/plugin.json .claude-plugin/marketplace.json hooks/hooks.json types/index.d.ts \
         skills/orchestrate/SKILL.md skills/orchestrate/scripts/ask_opus.sh \
         skills/orchestrate/templates/brief.md skills/orchestrate/templates/board.md skills/orchestrate/templates/tier-log.md tests/ladder.test.ts; do
  [[ -f "$repo_root/$f" ]] && ok "present $f" || bad "missing $f"
done
[[ -f "$mod" ]] && ok "present hooks/ladder.mjs" || bad "missing hooks/ladder.mjs (the hooks module)"

for j in .claude-plugin/plugin.json .claude-plugin/marketplace.json hooks/hooks.json; do
  python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$repo_root/$j" 2>/dev/null && ok "json $j" || bad "json $j"
done
python3 - "$repo_root" <<'EOF' && ok "manifest names ladder and the module" || bad "manifest names ladder and the module"
import json, sys, os
r = sys.argv[1]
p = json.load(open(os.path.join(r, ".claude-plugin/plugin.json")))
m = json.load(open(os.path.join(r, ".claude-plugin/marketplace.json")))
h = json.load(open(os.path.join(r, "hooks/hooks.json")))
assert p["name"] == "ladder" and p.get("types") == "./types/index.d.ts"
assert any(x["name"] == "ladder" for x in m["plugins"])
assert h["modules"] == ["./ladder.mjs"]
for k in ("enabled", "ceiling", "floor", "upSwitch", "holdTurns", "minChars", "classifier"):
    assert k in p["userConfig"], k
assert p["userConfig"]["floor"]["default"] == "sonnet"
EOF

# Shell
for f in "$skill/scripts/ask_opus.sh" "$repo_root/tests/test_skill.sh"; do
  bash -n "$f" && ok "syntax $(basename "$f")" || bad "syntax $(basename "$f")"
done
[[ -x "$skill/scripts/ask_opus.sh" ]] && ok "helper executable" || bad "helper executable"

# Routing strings: Fable on top, Opus as planner, Sonnet default, Haiku bulk.
head -1 "$skill/SKILL.md" | grep -qx -- '---' && grep -q '^name: orchestrate$' "$skill/SKILL.md" \
  && ok "frontmatter" || bad "frontmatter"
for s in '| Conductor | Fable 5.1' 'claude-opus-5-5' 'Sonnet 5.5' 'Haiku 4.5' 'Opus 5.5 speaks:' 'Linear by default' 'Worker report format' 'Never route a worker to Fable' 'Choosing and correcting the tier' 'Scaling up' 'Scaling down' 'one rung up' 'At most two escalations per node' 'Tier: right | too low' 'tier-log.md' 'a tier problem. Fix the brief'; do
  grep -q -- "$s" "$skill/SKILL.md" && ok "SKILL.md has '$s'" || bad "SKILL.md missing '$s'"
done
for s in 'Prior attempt:' 'score <0-10>' 'Tier line'; do
  grep -q -- "$s" "$skill/templates/brief.md" && ok "brief has '$s'" || bad "brief missing '$s'"
done
grep -q -- '| Score | Tier |' "$skill/templates/board.md" && ok "board has score and tier" || bad "board missing score and tier"
python3 - "$skill/SKILL.md" <<'EOF' && ok "rubric bands cover 0-10 with no gap" || bad "rubric bands"
import re, sys
s = open(sys.argv[1]).read()
m = re.search(r"Total 0 to (\d+) goes to `haiku`, (\d+) to (\d+) to `sonnet`, (\d+) and up to `opus`", s)
assert m, "band sentence"
a, b, c, d = map(int, m.groups())
assert b == a + 1 and d == c + 1 and d <= 10
assert s.count("| 0 | 1 | 2 |") == 1 and len(re.findall(r"^\| (Spec:|Reasoning depth \||Blast radius|Context to hold|Novelty \|)", s, re.M)) == 5
EOF
for s in 'sonnet (Sonnet 5.5)' 'haiku (Haiku 4.5)' 'claude-opus-5-5'; do
  grep -q -- "$s" "$skill/scripts/ask_opus.sh" && ok "helper has '$s'" || bad "helper missing '$s'"
done
if [[ -f "$mod" ]]; then
  for s in 'const TIERS = \["haiku", "sonnet", "opus", "fable"\]' 'prompt.submit' 'prompt.compose' 'asUser: true' 'command: "model"' 'ladder:policy' '.catch((\$, e, next) => next(e))' 'scheduleUpdateCheck(\$, e, cfg)' 're-dispatch one' 'next similar task one tier lower'; do
    grep -q -- "$s" "$mod" && ok "module has '$s'" || bad "module missing '$s'"
  done
fi
upd="$mod"
if [[ -f "$upd" ]]; then
  for s in 'raw.githubusercontent.com/calebgrimes/fable-ladder/main/.claude-plugin/plugin.json' '"--ff-only"' '"claude", "plugin", "update"' 'updates.lastCheck' 'e.isInteractive !== true'; do
    grep -q -- "$s" "$upd" && ok "updater has '$s'" || bad "updater missing '$s'"
  done
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); u=d['userConfig']; assert u['updates']['default']=='notify' and set(u['updates']['options'])=={'notify','apply','off'} and u['updateEveryDays']['default']==21" "$repo_root/.claude-plugin/plugin.json" \
    && ok "manifest update rows" || bad "manifest update rows"
fi

# House style and hygiene, repo-wide.
if grep -rIiqE 'gpt|deepseek|codex|opencode' "$skill" "$repo_root/hooks" "$repo_root/types"; then bad "foreign model names"; else ok "no foreign model names"; fi
if grep -rIq $'\xe2\x80\x94' --exclude-dir=.git "$repo_root"; then bad "em dash found"; else ok "no em dashes"; fi
if grep -rIqE 'sk-ant-|ghp_|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|/Users/[a-z]' --exclude-dir=.git --exclude=.git --exclude=test_skill.sh "$repo_root"; then
  bad "credential or personal path found"; else ok "no credentials or personal paths"; fi

rc=0; printf '' | "$skill/scripts/ask_opus.sh" >/dev/null 2>&1 || rc=$?; [[ $rc -eq 64 ]] && ok "empty packet refused" || bad "empty packet refused (rc=$rc)"

# The engine's own reads of the plugin. Skipped with a note when claude is absent.
if command -v claude >/dev/null 2>&1 && [[ -f "$mod" ]]; then
  if out="$(claude plugin validate "$repo_root" 2>&1)"; then ok "claude plugin validate"; else bad "claude plugin validate"; printf '%s\n' "$out" | tail -20; fi
  if [[ "${1:-}" != "--quick" ]]; then
    if out="$(claude plugin test "$repo_root" 2>&1)"; then ok "claude plugin test"; printf '%s\n' "$out" | grep -E "pass|fail|✓|✗" | tail -15; else bad "claude plugin test"; printf '%s\n' "$out" | tail -40; fi
  fi
else
  printf 'skip  claude plugin validate/test (claude on PATH: %s, module present: %s)\n' "$(command -v claude >/dev/null && echo yes || echo no)" "$([[ -f "$mod" ]] && echo yes || echo no)"
fi

exit $fail
