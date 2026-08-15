#!/bin/bash
# Tier-3 lead stand-in (backlog item 6c, docs/spec.md §8) -- always replies
# in plain prose with no fenced ```json block at all, every round,
# regardless of prompt content. Exercises the malformed-retry-then-escalate
# path (TEAM_LEAD_MALFORMED_RETRY_BUDGET) end to end against a real
# tmux/agent_run() call, without needing a real aider CLI.
echo "I have thought about this and decided not to use any tool right now."
