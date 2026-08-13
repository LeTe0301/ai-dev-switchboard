#!/bin/bash
# Tier-3 lead stand-in (backlog item 6c, docs/spec.md §8) -- aider is not
# installed in this environment, so this fixture stands in for a real
# prose-parse-tier engine. Deterministic and stateless: it reads the round
# number out of the assembled prompt's own literal "Round N of M" text
# (_round_context()'s own wording) rather than keeping any state of its
# own, and switches its canned answer accordingly -- round 1 emits a
# well-formed fact_check call, every later round emits a well-formed
# finish call. Drives a real, complete team_run() end to end (fact_check
# then finish) using nothing but a shell script and this switchboard's own
# real tmux/agent_run() machinery -- no real aider CLI involved.
prompt="$1"
if [[ "$prompt" == *"Round 1 of"* ]]; then
    cat <<'EOF'
I will check a claim first.
```json
{"tool": "fact_check", "args": {"claim": "todo list"}}
```
EOF
else
    cat <<'EOF'
The work is done.
```json
{"tool": "finish", "args": {"summary": "stand-in finished after one fact_check round"}}
```
EOF
fi
