#!/bin/bash
# Tier-3 lead stand-in (backlog item 6c, docs/spec.md §8) -- always replies
# with a real fenced ```json block whose contents are NOT valid JSON
# (a truncated/malformed object), every round. Distinguishes "a fence
# exists but its contents don't parse" from tier3_stub_no_fence.sh's "no
# fence at all" -- both are malformed, but via different code paths inside
# _parse_tier3_action().
cat <<'EOF'
Here is my answer.
```json
{"tool": "finish", "args": {summary: "missing quotes around this key"}
```
EOF
