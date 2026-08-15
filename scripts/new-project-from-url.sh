#!/usr/bin/env bash
# Privileged hand-off for clone_project_from_url() (backlog item 16,
# docs/spec.md). Installed UNCONDITIONALLY (like new-project-from-upload.sh)
# -- clone-from-URL never depends on --with-git-hosting.
#
# Usage: new-project-from-url.sh <url> <name>
set -euo pipefail

CONFIG=/etc/ai-dev-switchboard/switchboard.env
[ -f "$CONFIG" ] && source "$CONFIG"
RUN_USER="${RUN_USER:-dev}"
PROJECTS_DIR="${PROJECTS_DIR:-/home/${RUN_USER}/projects}"
CLONE_MAX_BYTES="${CLONE_MAX_BYTES:-524288000}"

if [ $# -ne 2 ]; then
    echo "Usage: $0 <url> <name>" >&2
    exit 1
fi
URL="$1"
NAME="$2"

# Defense in depth -- same discipline as the two sibling scripts.
if ! [[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9\ _-]{0,59}$ ]]; then
    echo "Invalid project name: $NAME" >&2
    exit 1
fi
# Same allowlist app.py's _validate_clone_url() already enforced -- re-
# checked here in bash's own regex/pattern-matching engine, never trusted
# blindly.
#
# A first version of this check used a negative-lookahead-shaped guard
# (reject '-' immediately after the scheme, or immediately after '@' for
# the scp-like shorthand) to block the host-injection shape
# (CVE-2017-1000117). A review found that insufficient: both accepted
# grammars allow an optional segment (a `user@` prefix for `scheme://`, a
# `:path` suffix for the scp-like shorthand) between the character actually
# checked and the component that matters to ssh/git, so
# `ssh://user@-oProxyCommand=.../x` and `user@host:-oProxyCommand=...` both
# slipped past and reached a real `git clone` invocation below, protected
# only by installed git's own downstream hostname/pathname hardening -- not
# by this script.
#
# This version instead isolates the real host (and, for scp-like
# shorthand, the real path) component via parameter expansion -- mirroring
# app.py's _clone_url_host_is_safe()/urlsplit()-based logic, since bash has
# no urllib.parse of its own -- and validates that isolated substring
# specifically. See _host_is_safe() below for the actual decision.

# _host_is_safe HOST -- true (status 0) only for a syntactically legitimate
# hostname, IPv4 literal, or IPv6 literal; false for anything empty, a
# leading '-', or any other shape ssh/git wouldn't treat as a real host.
# Mirrors app.py's _clone_url_host_is_safe().
_host_is_safe() {
    local host="$1"
    [ -n "$host" ] || return 1
    case "$host" in
        -*) return 1 ;;
    esac
    if [[ "$host" == *:* ]]; then
        # Only ever legitimate for an IPv6 literal -- hex digits, ':', and
        # an optional '%<scope-id>' suffix, same shape ipaddress.ip_address()
        # accepts on the app.py side. Anything else that happens to contain
        # a ':' (e.g. a smuggled second host/path component) is rejected.
        [[ "$host" =~ ^[0-9A-Fa-f:]+(%[A-Za-z0-9_.-]+)?$ ]] && return 0
        return 1
    fi
    [[ "$host" =~ ^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$ ]] && return 0
    return 1
}

URL_OK=0
if [[ "$URL" =~ ^(https?|ssh)://[^[:space:]]+$ ]]; then
    # Isolate the authority (scheme://<here>/path), then the host within
    # it: strip an optional user@ prefix by keeping only what follows the
    # LAST unescaped '@' (RFC 3986 userinfo semantics -- same as Python's
    # urlsplit()), then either unwrap a bracketed IPv6 literal or strip an
    # optional trailing :port.
    rest="${URL#*://}"
    authority="${rest%%/*}"
    hostport="${authority##*@}"
    if [[ "$hostport" == \[*\]* ]]; then
        host="${hostport#\[}"
        host="${host%%]*}"
    else
        host="${hostport%:*}"
    fi
    _host_is_safe "$host" && URL_OK=1
elif [[ "$URL" =~ ^[A-Za-z0-9_.-]+@[^[:space:]]+:[^[:space:]].*$ ]]; then
    # scp-like shorthand: split on the first '@' (the regex above already
    # anchors the user segment to a charset with no '@'/':' in it, so the
    # first '@' is unambiguous), then the first ':' after that splits the
    # real host from the real path. Both matter here (unlike the scheme
    # case): host is ssh's connection target, path is what git hands to
    # the remote git-upload-pack invocation -- a leading '-' on either is
    # the injection shape.
    rest="${URL#*@}"
    host="${rest%%:*}"
    path="${rest#*:}"
    if _host_is_safe "$host" && [ -n "$path" ] && [[ "${path:0:1}" != "-" ]]; then
        URL_OK=1
    fi
fi
if [ "$URL_OK" -ne 1 ]; then
    echo "Unsupported URL: $URL" >&2
    exit 1
fi

mkdir -p "$PROJECTS_DIR"
DEST="${PROJECTS_DIR}/${NAME}"

# Atomic, no -p -- closes the same TOCTOU race the sibling scripts close.
if ! mkdir "$DEST"; then
    echo "Already exists: $DEST" >&2
    exit 1
fi
chown "$RUN_USER:$RUN_USER" "$DEST"

# DEVIATION 1 from new-project-from-gitea.sh's "leave a partial clone in
# place for manual cleanup" precedent: always remove DEST on any failure
# below. Reasoning: unlike a Gitea repo this switchboard just created
# itself (tiny, essentially always succeeds) or a local cp -a (fast,
# near-atomic), an arbitrary external clone is the one creation path
# genuinely likely to fail non-trivially partway through a large transfer
# (network drop, timeout, oversized) -- leaving a large partial .git
# directory behind by default is worse here than the sibling scripts'
# assumption holds for.
#
# Deliberately an EXIT trap, not ERR: every failure branch below exits via
# an explicit `exit 1` (inside an `... || { ...; exit 1; }` block, or after
# the size-cap check), and bash's ERR trap does NOT fire for an explicit
# `exit` builtin -- only for a command whose own nonzero status would
# itself trigger `set -e` (verified directly against this script's own
# shape; an ERR trap here would silently never run, defeating this
# deviation's whole purpose). EXIT fires on every shell exit regardless of
# cause, so it's cleared with `trap - EXIT` right before the final success
# echo, same shape the old `trap - ERR` line used.
cleanup() { rm -rf "$DEST"; }
trap cleanup EXIT

# DEVIATION 2: the clone URL is arbitrary, attacker-influenced input --
# unlike new-project-from-gitea.sh's $CLONE_URL (built server-side from
# already-regex-constrained $OWNER/$REPO), it must NEVER be interpolated
# into a string that gets re-parsed by a shell. This idiom -- the entire
# -c script is single-quoted (fully literal, zero interpolation by THIS
# shell), and $URL/$DEST are passed as su's own trailing positional
# arguments, which su forwards to `bash -c` where they become that
# invocation's own $1/$2 -- is the standard safe way to hand untrusted
# values to `sh -c`/`su -c` without ever building a shell string out of
# them. (Confirmed against su(1): "When user is specified, additional
# arguments can be supplied, in which case they are passed to the shell.")
#
# GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS=/bin/false: never block waiting on an
# interactive username/password prompt -- this cycle deliberately doesn't
# support HTTPS+token auth (docs/spec.md "Private-repo auth"), so a private
# HTTPS repo must fail fast and clearly, not hang until CLONE_TIMEOUT_SECONDS.
# GIT_SSH_COMMAND's BatchMode=yes does the same for an SSH password/
# passphrase prompt AND an unknown-host-key confirmation prompt
# (accept-new = trust-on-first-use instead of an interactive yes/no).
# GIT_ALLOW_PROTOCOL is a second, git-side allowlist enforcement --
# redundant with the regex checks above, but cheap insurance against
# ext::/fd:: transport-helper-style RCE shapes even if a future refactor
# ever loosens the regex without noticing this comment.
CLONE_OUTPUT=$(su "$RUN_USER" -s /bin/bash -c \
    'GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false \
     GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15" \
     GIT_ALLOW_PROTOCOL="http:https:ssh" \
     git clone -- "$1" "$2"' _ "$URL" "$DEST" 2>&1) || {
    echo "git clone failed:" >&2
    echo "$CLONE_OUTPUT" >&2
    exit 1
}

# DEVIATION 3 / new: post-clone size cap. Git has no way to cap a clone's
# size up front -- checked AFTER the fact instead, same "checked, and
# rejected after the fact" idea as UPLOAD_MAX_BYTES's own post-decompression
# check (docs/spec.md "Size limits" in item 3's own spec history).
SIZE=$(du -sb "$DEST" 2>/dev/null | cut -f1)
if [ -n "$SIZE" ] && [ "$SIZE" -gt "$CLONE_MAX_BYTES" ]; then
    echo "Cloned repository is ${SIZE} bytes, over the ${CLONE_MAX_BYTES}-byte limit -- removed." >&2
    exit 1
fi

trap - EXIT
echo "Ready: $DEST — will show up in the web UI now."
