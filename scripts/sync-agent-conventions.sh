#!/bin/bash
#
# One canonical set of agent conventions, synced to wherever each tool looks
# for it.
#
# agents/_conventions.md is the source. The pipeline already injects it into
# every dispatch, so a role running on Claude, Codex, aider or a local model
# gets it regardless of engine. But the same agents are also used OUTSIDE the
# pipeline -- Claude Code opened directly in a project, Kilo, Codex CLI -- and
# each of those reads its own file in its own location. Without this, those
# copies drift and an agent behaves differently depending on which tool
# launched it.
#
# Copies are written with a header marking them generated, so nobody edits one
# by hand and loses the change on the next sync.
#
#   sync-agent-conventions.sh [--check] [target-dir ...]
#
#   --check   report drift and exit non-zero, changing nothing (for CI)
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$REPO/agents/_conventions.md"

[ -f "$SOURCE" ] || { echo "no source at $SOURCE" >&2; exit 2; }

CHECK=0
[ "${1:-}" = "--check" ] && { CHECK=1; shift; }

# Where each tool looks. A target is only written if its parent already exists,
# so this is safe to run on a box where a given tool is not installed.
TARGETS=(
  "$HOME/.claude/CLAUDE.md"          # Claude Code, user scope
  "$REPO/AGENTS.md"                  # Codex CLI, and anything reading AGENTS.md
  "$REPO/.kilo/AGENTS.md"            # Kilo
)
# Extra project directories may be passed as arguments; each gets all three.
for extra in "$@"; do
  TARGETS+=("$extra/CLAUDE.md" "$extra/AGENTS.md" "$extra/.kilo/AGENTS.md")
done

HEADER="<!-- GENERATED from agents/_conventions.md by scripts/sync-agent-conventions.sh.
     Do not edit this copy: the next sync overwrites it. Edit the source. -->
"

drift=0
written=0
for target in "${TARGETS[@]}"; do
  parent="$(dirname "$target")"
  # Only .kilo is created on demand; everything else must already exist, so we
  # never scatter files into directories the user has not opted into.
  case "$target" in
    */.kilo/AGENTS.md) [ -d "$(dirname "$parent")" ] || continue; mkdir -p "$parent" ;;
    *) [ -d "$parent" ] || continue ;;
  esac

  desired="$HEADER"$'\n'"$(cat "$SOURCE")"
  if [ -f "$target" ] && [ "$(cat "$target")" = "$desired" ]; then
    continue
  fi

  # A hand-written file with real project content must never be replaced.
  # Instead make it IMPORT the canonical conventions, so its own content
  # survives and the shared rules are still in effect. Claude Code resolves
  # an @path line in CLAUDE.md as an import.
  if [ -f "$target" ] && ! head -1 "$target" | grep -q "GENERATED from agents/_conventions.md"; then
    if grep -qF "@$SOURCE" "$target"; then
      continue
    fi
    if [ "$CHECK" = "1" ]; then
      echo "DRIFT $target (hand-written, missing the import line)"
      drift=1
    else
      printf '\n<!-- shared agent conventions, kept in one place -->\n@%s\n' "$SOURCE" >> "$target"
      echo "imported into $target (hand-written, content preserved)"
      written=$((written + 1))
    fi
    continue
  fi

  if [ "$CHECK" = "1" ]; then
    echo "DRIFT $target"
    drift=1
  else
    printf '%s' "$desired" > "$target"
    echo "wrote $target"
    written=$((written + 1))
  fi
done

if [ "$CHECK" = "1" ]; then
  [ "$drift" = "0" ] && echo "all copies current"
  exit "$drift"
fi
echo "synced $written cop$([ "$written" = "1" ] && echo y || echo ies) from agents/_conventions.md"
