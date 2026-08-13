#!/bin/bash
# Tier-3 lead stand-in (backlog item 6c, docs/spec.md §11 acceptance
# criterion: "ask_user blocks the loop... team-resolve answers it and the
# loop resumes from the persisted state... proven by resolving in a
# SEPARATE PROCESS INVOCATION from the one that blocked"). Deterministic,
# stateless, driven entirely by the assembled prompt's own literal text:
#   - "Round 1 of..."      -> fact_check (bumps action_count above 0, so a
#                              later finish is never rejected as premature)
#   - prompt has "answered:" (the round-history one-liner
#     _cli_team_resolve()'s own history entry renders, e.g. "round 2:
#     ask_user_resolved(...) -> answered: Yes") -> finish
#   - otherwise                                 -> ask_user
prompt="$1"
if [[ "$prompt" == *"Round 1 of"* ]]; then
    cat <<'EOF'
```json
{"tool": "fact_check", "args": {"claim": "todo list"}}
```
EOF
elif [[ "$prompt" == *"answered:"* ]]; then
    cat <<'EOF'
```json
{"tool": "finish", "args": {"summary": "resumed in a separate process and finished after the human answered"}}
```
EOF
else
    cat <<'EOF'
```json
{"tool": "ask_user", "args": {"question": "Should I proceed?", "header": "Proceed",
  "options": [{"label": "Yes", "description": "go ahead"}, {"label": "No", "description": "stop here"}]}}
```
EOF
fi
