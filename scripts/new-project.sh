#!/usr/bin/env bash
# One-shot: create a new private repo AND have it show up as a toggleable
# project immediately, auto-synced on every future push to main. This is
# the fast path the web UI's own "+ New project" button calls — new-repo.sh
# / new-dev-instance.sh still work standalone for more control (e.g.
# auto-deploy to another machine on push).
#
# Usage: new-project.sh <name>
set -euo pipefail
if [ $# -ne 1 ]; then
    echo "Usage: $0 <name>" >&2
    exit 1
fi
/usr/local/bin/ai-dev-switchboard-new-repo.sh "$1"
/usr/local/bin/ai-dev-switchboard-new-dev-instance.sh "$1"
