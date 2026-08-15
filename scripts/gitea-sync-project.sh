#!/usr/bin/env bash
# Low-privilege sync-on-push logic (docs/spec.md part 2c part 1, "The sync
# decision"). Dispatched by app.py's poll machinery (_gitea_sync_bg /
# _gitea_sync_run) whenever a project's polled remote branch SHA has moved
# since it was last checked, run via `sudo -u $RUN_USER` -- NOT root,
# unlike new-project-from-gitea.sh: nothing here needs to chown or create a
# new directory, the working copy and its ownership already exist.
#
# Never destructive: a `git fetch` always runs (it only ever updates
# refs/remotes/origin/$BRANCH, never the working tree or HEAD), but the
# working tree/HEAD themselves are only ever touched by a guaranteed
# no-op-or-clean fast-forward (`git merge --ff-only`) -- never
# `git reset --hard`. See the deleted project-sync.sh (git show
# dcc582b:scripts/project-sync.sh) for the unconditional-reset behavior
# this deliberately does NOT carry forward.
#
# Usage: gitea-sync-project.sh <name> <branch>
#
# On success (exit 0), prints exactly one line naming the outcome:
#   synced           -- fast-forwarded (or already up to date -- both are
#                       the same no-op-or-real merge --ff-only call)
#   skipped-dirty    -- uncommitted changes in the working tree; nothing
#                       beyond the fetch was touched
#   skipped-diverged -- local HEAD isn't an ancestor of the fetched ref
#                       (genuinely diverged, or local commits not yet
#                       pushed); nothing beyond the fetch was touched
#   no-such-project  -- PROJECTS_DIR/<name> doesn't exist, or isn't a git
#                       working copy -- the repo-map entry that pointed here
#                       is stale (e.g. the project was removed by hand);
#                       harmless, not treated as an error
# A non-zero exit means an argument/config problem, or the `git fetch`
# itself failed (e.g. Gitea briefly unreachable) -- the caller leaves the
# repo-map's remote_sha untouched in that case, so the next poll interval
# retries automatically rather than silently giving up.
set -euo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
RUN_USER="${RUN_USER:-dev}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${RUN_USER}/projects}"

if [ $# -ne 2 ]; then
    echo "Usage: $0 <name> <branch>" >&2
    exit 1
fi

NAME="$1"
BRANCH="$2"

# Defense in depth: re-validate both arguments even though app.py already
# validated <name> against its own NAME_RE before ever writing a repo-map
# entry -- never trust the caller blindly, same discipline every other
# privileged/semi-privileged script in this repo already follows (see
# scripts/new-project-from-gitea.sh). <branch> gets a cheap sanity check
# since it's about to be interpolated into git arguments.
if ! [[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9\ _-]{0,59}$ ]]; then
    echo "Invalid project name: $NAME" >&2
    exit 1
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo "Invalid branch name: $BRANCH" >&2
    exit 1
fi

DEST="${PROJECTS_DIR}/${NAME}"

# Not an error -- a repo-map entry can outlive the project directory it
# points at (no delete-project feature exists to clean it up; see
# docs/spec.md "Edge cases"). Retried harmlessly every poll interval.
if [ ! -d "$DEST/.git" ]; then
    echo "no-such-project"
    exit 0
fi

cd "$DEST"

# Plain fetch -- never touches the working tree or HEAD, only updates
# refs/remotes/origin/$BRANCH. Left to fail loudly (set -e) rather than
# swallowed: a transient fetch failure shouldn't be recorded as any of the
# three real outcomes below, and the caller already treats a non-zero exit
# here as "don't update remote_sha, retry next interval".
git fetch origin "$BRANCH"

# Dirty check -- independent of the ancestor check below (a clean tree can
# still be diverged; an ancestor tree can still be dirty), so both run
# regardless of each other's outcome.
if [ -n "$(git status --porcelain)" ]; then
    echo "skipped-dirty"
    exit 0
fi

# Fast-forward-safety check -- local HEAD must be a strict/equal ancestor
# of the newly fetched ref. A genuinely diverged history and "local commits
# not yet pushed" both fail this the same way, and both get the same safe
# treatment: stop here, touch nothing.
if ! git merge-base --is-ancestor HEAD "origin/$BRANCH"; then
    echo "skipped-diverged"
    exit 0
fi

# A guaranteed no-op-or-clean fast-forward, given the ancestor check above
# already confirmed it's possible -- chosen over `git reset --hard`
# specifically because it refuses loudly instead of silently discarding
# anything.
git merge --ff-only "origin/$BRANCH" >/dev/null
echo "synced"
