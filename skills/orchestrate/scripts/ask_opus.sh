#!/usr/bin/env bash
set -euo pipefail

if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code CLI is not available on PATH." >&2
  exit 127
fi

if [[ $# -gt 0 ]]; then
  packet="$*"
else
  packet="$(cat)"
fi

if [[ -z "${packet//[[:space:]]/}" ]]; then
  echo "Provide a non-empty orchestration packet on stdin or as arguments." >&2
  exit 64
fi

system_prompt='You are Claude Opus 5.5, the orchestration controller for a Claude Code session. You plan and adjudicate only; never assign yourself implementation. Use only the supplied packet. Return a concise executable task graph, not implementation. For each node specify: id, purpose, dependencies, model, exclusive file or responsibility ownership, expected output, verification, and stop condition. The only allowed worker models are sonnet (Sonnet 5.5), haiku (Haiku 4.5), and opus (Opus 5.5). Never route a node to any other model.

Routing, applied only when the user did not explicitly choose an allowed route: loop construction, repeated iteration, and high-throughput mechanical work use haiku. Implementation with clear acceptance criteria uses sonnet; Sonnet 5.5 is reliable, so give it a wide lane. Use opus only for genuinely tricky nodes such as concurrency, subtle algorithms, hard debugging, or adversarial review. Research and review use normal task fit, cheapest tier first. When unsure between tiers, pick the cheaper one.

Dispatch is linear unless the packet says parallel: order nodes so each can run after the previous one reports. If parallel is requested, mark which nodes may run together, at most 4 at once. Minimize the number of agents. Preserve the user scope and approval boundaries. End with an integration and final-verification node. Do not expose chain-of-thought; give decisions and brief rationale only. Do not use em dashes.

Start the answer with one short line per assignment in the form: Node id: model: bounded responsibility.'

effort="${ORCH_EFFORT:-medium}"
if [[ -n "${ORCH_MODEL:-}" ]]; then
  model_candidates=("$ORCH_MODEL")
else
  model_candidates=("claude-opus-5-5" "opus")
fi

response=""
selected_model=""
last_error=""
for model in "${model_candidates[@]}"; do
  set +e
  candidate_response="$(claude \
    --print \
    --model "$model" \
    --effort "$effort" \
    --permission-mode dontAsk \
    --tools "" \
    --no-session-persistence \
    --output-format text \
    --system-prompt "$system_prompt" \
    "$packet" 2>&1)"
  candidate_status=$?
  set -e
  if [[ $candidate_status -eq 0 ]]; then
    response="$candidate_response"
    selected_model="$model"
    break
  fi
  last_error="$candidate_response"
done

if [[ -z "$selected_model" ]]; then
  echo "The Opus planner could not be reached. Tried: ${model_candidates[*]}." >&2
  [[ -n "$last_error" ]] && printf 'Last error: %s\n' "$last_error" >&2
  exit 69
fi

printf 'Opus 5.5 speaks (%s):\n\n%s\n' "$selected_model" "$response"
