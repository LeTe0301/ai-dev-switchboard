#!/usr/bin/env python3
"""
Push a local docs/spec.md into a running Taiga instance as a new userstory —
one direction only (spec -> Taiga), see docs/spec.md "Local backlog tracker
(Taiga) -- part 1b: push a spec into Taiga". Stdlib-only (urllib.request,
json, argparse, os) -- no python-dotenv, no requests; the credentials file is
parsed manually as plain KEY=value lines, matching engines.d/*.engine's own
_parse_engine_file() convention in app/app.py rather than pulling in
configparser.

Usage:
    python3 scripts/taiga_push_spec.py [--spec PATH] [--project SLUG]
                                        [--config PATH] [--dry-run] [--verify]

See scripts/taiga-configure-push.sh for the one-time interactive setup step
that creates ~/.config/ai-dev-switchboard/taiga-push.env, which this script
reads (TAIGA_URL, TAIGA_USERNAME, TAIGA_PASSWORD, TAIGA_PROJECT_SLUG).

Only ever creates a new userstory -- never updates/deduplicates, never reads
anything back from Taiga into docs/spec.md (see docs/spec.md "Non-goals").
"""
import argparse
import datetime
import json
import os
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/ai-dev-switchboard/taiga-push.env")
DEFAULT_SPEC_PATH = "docs/spec.md"


class TaigaPushError(Exception):
    """Raised for any failure surfaced to main() as a single clear stderr
    line (no traceback). main() is the only place that catches this."""


class TaigaConnectionError(TaigaPushError):
    """Taiga couldn't be reached at all (connection refused, DNS failure,
    timeout, malformed URL, ...) -- distinct from a non-2xx HTTP response."""


class TaigaHTTPError(TaigaPushError):
    """Taiga responded with a non-2xx status. Callers inspect .status to
    decide which of this script's user-facing edge-case messages applies."""

    def __init__(self, status: int, body: str = ""):
        self.status = status
        self.body = body
        super().__init__(f"Taiga returned HTTP {status}")


# Internal marker exceptions raised by the three endpoint-level functions
# below (_authenticate/_lookup_project/_create_userstory) once they've
# interpreted a TaigaHTTPError -- kept separate from the exact user-facing
# wording (docs/spec.md "Edge cases") so that wording lives in one place
# (_run(), which also knows the config path to embed in the message) rather
# than being duplicated across every call site.
class _AuthRejected(TaigaPushError):
    pass


class _ProjectNotFound(TaigaPushError):
    def __init__(self, slug: str):
        self.slug = slug
        super().__init__(f"no such project: {slug}")


def _taiga_request(base_url: str, method: str, path: str, token: str = None, body: dict = None):
    """
    One shared urllib.request wrapper: builds the request, sets
    Authorization if token is given, sends JSON, parses the JSON response,
    and raises a TaigaPushError subclass on any non-2xx status
    (TaigaHTTPError) or connection failure (TaigaConnectionError). This is
    the one function the test suite monkeypatches (tests/test_taiga_push.py)
    rather than mocking urllib.request globally -- same seam-per-shelled-out-
    call convention as tests/test_taiga.py monkeypatching appmod.taiga_run.

    Deliberately never includes the raw Taiga response body in a message
    that could propagate to the user (see TaigaHTTPError.body, which callers
    must not print) -- the acceptance criterion that a bad-credentials error
    must never echo the configured password back holds even if some future
    Taiga version were to (incorrectly) reflect request data in an error
    body.
    """
    url = f"{base_url}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        # Request(...) construction (not just urlopen()) is inside this try:
        # a blank/malformed base_url (e.g. an empty or scheme-less
        # TAIGA_URL) makes Request.__init__ itself raise a bare ValueError
        # while parsing the URL, before any socket is even opened -- that
        # must be caught here too, not just failures from the actual
        # network call, or it propagates uncaught past every TaigaPushError
        # handler (docs/test-review.md Defect 1).
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", "replace")
        except Exception:
            err_body = ""
        raise TaigaHTTPError(e.code, err_body)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TaigaConnectionError(str(e))
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _load_config(path: str) -> dict:
    """Parses the KEY=value credentials file (docs/spec.md "Proposed
    approach"). Never raises KeyError for a missing/blank value -- callers
    read via dict.get(..., "") so an incomplete file surfaces as the same
    "bad credentials"-shaped error Taiga's own API would produce, not a
    traceback (docs/spec.md "Edge cases")."""
    if not os.path.isfile(path):
        raise TaigaPushError(
            f"No Taiga push config found at {path} — run scripts/taiga-configure-push.sh first.")
    cfg = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                cfg[key.strip()] = value.strip()
    except OSError:
        raise TaigaPushError(
            f"No Taiga push config found at {path} — run scripts/taiga-configure-push.sh first.")
    return cfg


def _parse_acl_other_bits(getfacl_output: str) -> dict | None:
    """Parses `getfacl -p <path>` output. Returns None if the file has only
    a minimal ACL (no `mask::` line -- st_mode's group/other bits are then
    accurate and the plain check below applies unchanged). Returns
    {"other": 0-7 int, "other_str": "r--"} from the `other::` line when an
    extended ACL is present -- the only entry that reflects genuine
    world-exposure once a mask entry exists (item 37: the group-class bits
    stat() reports become the ACL mask, not real group permissions, once
    any named user/group ACL entry -- e.g. item 29's switchboard-svc:r --
    is present, so they must never drive this warning)."""
    lines = getfacl_output.splitlines()
    if not any(l.startswith("mask::") for l in lines):
        return None
    other_line = next((l for l in lines if l.startswith("other::")), None)
    if other_line is None:
        return None
    other_str = other_line.split("::", 1)[1].strip()
    bits = (4 if "r" in other_str else 0) | (2 if "w" in other_str else 0) | (1 if "x" in other_str else 0)
    return {"other": bits, "other_str": other_str}


def _read_getfacl(path: str) -> str | None:
    """The one seam this ACL check monkeypatches in tests (mirrors
    _taiga_request's own "one seam per shelled-out call" convention).
    Returns None (not a raised exception) if `getfacl` isn't installed or
    the call otherwise fails -- best-effort, same as taiga-configure-
    push.sh's own `command -v setfacl` fallback -- so a host without the
    'acl' package degrades to the plain st_mode check below, not a crash."""
    try:
        result = subprocess.run(["getfacl", "-p", path], capture_output=True,
                                 text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _check_config_permissions(path: str) -> None:
    """docs/spec.md "Edge cases": a config file mode looser than 600 is a
    loud warning, not a hard block -- this file holds a live, standalone
    Taiga password with no other secret co-located to notice a leak via,
    unlike switchboard.env. Item 37: once the file carries an extended ACL
    (e.g. item 29's `setfacl -m u:switchboard-svc:r` grant), stat()'s
    group-class bits report the ACL *mask*, not real group permissions --
    so this consults `getfacl`'s `other::` entry instead whenever an ACL is
    present, rather than misreading the mask as a loose group grant and
    recommending a `chmod` that would collapse it."""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return
    raw = _read_getfacl(path)
    acl = _parse_acl_other_bits(raw) if raw is not None else None
    if acl is not None:
        if acl["other"] != 0:
            print(
                f"WARNING: {path} is readable/writable by 'other' via its ACL "
                f"(other::{acl['other_str']}) — it holds a live Taiga password. "
                f"Run: setfacl -m o::--- {path}  (do NOT run chmod -- with an ACL "
                f"present, chmod recomputes the ACL mask and can silently break "
                f"a legitimate named grant, e.g. a service account's read access).",
                file=sys.stderr,
            )
        return  # ACL present and other:: clean -- narrowly-ACL'd, not loose
    if mode & 0o077:
        print(  # unchanged from today
            f"WARNING: {path} is readable by group/other (mode {oct(mode)}) — it holds a "
            f"live Taiga password. Run: chmod 600 {path}",
            file=sys.stderr,
        )


def _authenticate(base_url: str, username: str, password: str) -> str:
    """POST /api/v1/auth -> bearer auth_token. A fresh token is exchanged on
    every invocation (docs/spec.md "Background": no long-lived/cached token,
    each invocation is a short-lived one-shot process)."""
    try:
        data = _taiga_request(base_url, "POST", "/api/v1/auth",
                               body={"type": "normal", "username": username, "password": password})
    except TaigaHTTPError:
        raise _AuthRejected()
    token = data.get("auth_token")
    if not token:
        raise _AuthRejected()
    return token


def _lookup_project(base_url: str, token: str, slug: str) -> int:
    """GET /api/v1/projects/by_slug?slug=<slug> -> numeric project id."""
    try:
        data = _taiga_request(base_url, "GET",
                               f"/api/v1/projects/by_slug?slug={urllib.parse.quote(slug)}",
                               token=token)
    except TaigaHTTPError as e:
        if e.status == 404:
            raise _ProjectNotFound(slug)
        if e.status in (401, 403):
            raise _AuthRejected()
        raise
    return data["id"]


def _create_userstory(base_url: str, token: str, project_id: int, subject: str,
                       description: str) -> dict:
    """POST /api/v1/userstories -> the created userstory (id, ref, ...)."""
    return _taiga_request(base_url, "POST", "/api/v1/userstories", token=token,
                           body={"project": project_id, "subject": subject,
                                 "description": description})


def _build_subject_and_description(spec_text: str, spec_path: str) -> tuple:
    """
    Subject: the spec's own "# Spec: ..." first line with that prefix
    stripped; falls back to the file's first non-blank line, then to the
    filename itself, if the file doesn't follow that convention -- never
    errors out over a missing heading (docs/spec.md "Proposed approach").

    Description: the full raw spec text, with a short auto-generated footer
    appended (origin repo/path + UTC timestamp) for traceability, since
    there's no back-link the other direction (this is one-way).
    """
    lines = spec_text.splitlines()
    subject = ""
    if lines and lines[0].strip().startswith("# Spec:"):
        subject = lines[0].strip()[len("# Spec:"):].strip()
    if not subject:
        subject = next((line.strip() for line in lines if line.strip()), "")
    if not subject:
        subject = os.path.basename(spec_path)

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    origin_repo = os.path.basename(os.path.abspath(os.getcwd()))
    footer = (
        f"\n\n---\n_Pushed from `{spec_path}` in `{origin_repo}` on {timestamp} via "
        "ai-dev-switchboard's taiga_push_spec.py (one-way, spec -> Taiga only)._"
    )
    description = spec_text + footer
    return subject, description


# ─── user-facing edge-case messages (docs/spec.md "Edge cases") ───────────
# Kept as format strings in one place rather than inlined at each call site,
# since several of them need the config path embedded and are reused from
# more than one call site (--verify vs. the normal push path).
def _unreachable_msg(url: str, config_path: str) -> str:
    return (f"Could not reach Taiga at {url} — make sure it's toggled on in the "
            f"ai-dev-switchboard web UI, or check TAIGA_URL in {config_path}.")


def _bad_credentials_msg(config_path: str) -> str:
    return (f"Taiga rejected the configured username/password — check TAIGA_USERNAME/"
            f"TAIGA_PASSWORD in {config_path}, or re-run scripts/taiga-configure-push.sh.")


def _project_not_found_msg(slug: str) -> str:
    return (f"No Taiga project found with slug '{slug}' — create it first in Taiga's own "
            "web UI, or check TAIGA_PROJECT_SLUG / --project.")


def _no_spec_msg(path: str) -> str:
    return f"No spec found at {path} (or it's empty) — nothing to push."


def _authenticate_or_raise(base_url: str, username: str, password: str, config_path: str) -> str:
    try:
        return _authenticate(base_url, username, password)
    except TaigaConnectionError:
        raise TaigaPushError(_unreachable_msg(base_url, config_path))
    except _AuthRejected:
        raise TaigaPushError(_bad_credentials_msg(config_path))


def _lookup_project_or_raise(base_url: str, token: str, slug: str, config_path: str) -> int:
    try:
        return _lookup_project(base_url, token, slug)
    except TaigaConnectionError:
        raise TaigaPushError(_unreachable_msg(base_url, config_path))
    except _ProjectNotFound:
        raise TaigaPushError(_project_not_found_msg(slug))
    except _AuthRejected:
        raise TaigaPushError(_bad_credentials_msg(config_path))
    except TaigaHTTPError as e:
        # Any other non-2xx status _lookup_project didn't already translate
        # (404/401/403 above) -- degrades to a generic-but-safe message
        # (never includes e.body, which may echo request data) rather than
        # an unhandled TaigaHTTPError reaching main() with its bare
        # "Taiga returned HTTP <n>" wording (docs/test-review.md, follow-up
        # noted alongside Defect 1).
        raise TaigaPushError(f"Taiga rejected the project lookup (HTTP {e.status}).")


def _create_userstory_or_raise(base_url: str, token: str, project_id: int, subject: str,
                                description: str, config_path: str) -> dict:
    try:
        return _create_userstory(base_url, token, project_id, subject, description)
    except TaigaConnectionError:
        raise TaigaPushError(_unreachable_msg(base_url, config_path))
    except TaigaHTTPError as e:
        # docs/spec.md "Edge cases": a 401 here (despite a token just having
        # been issued) is surfaced as the same bad-credentials-shaped error
        # rather than an unhandled exception -- cheap insurance against a
        # genuinely short-lived token on some future Taiga version.
        if e.status == 401:
            raise TaigaPushError(_bad_credentials_msg(config_path))
        raise TaigaPushError(f"Taiga rejected the new userstory (HTTP {e.status}).")


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="taiga_push_spec.py",
        description="Push a local spec file into a running Taiga instance as a new "
                     "userstory (one-way, spec -> Taiga only).",
        epilog="If both --dry-run and --verify are passed, --verify wins: it only "
               "authenticates and looks up the project (no spec file is read, no "
               "dry-run preview is printed, no userstory is ever created).",
    )
    p.add_argument("--spec", default=DEFAULT_SPEC_PATH,
                    help=f"Path to the spec file to push, relative to CWD (default: {DEFAULT_SPEC_PATH}).")
    p.add_argument("--project", default=None,
                    help="Taiga project slug (default: TAIGA_PROJECT_SLUG from --config). "
                         "Overrides the config file for this one invocation only.")
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                    help=f"Path to the Taiga push credentials file (default: {DEFAULT_CONFIG_PATH}).")
    p.add_argument("--dry-run", action="store_true",
                    help="Do everything except the final POST /api/v1/userstories -- print the "
                         "subject/description that would be sent and exit 0.")
    p.add_argument("--verify", action="store_true",
                    help="Only authenticate and look up the project (no spec needed, no "
                         "userstory created). Wins over --dry-run if both are passed.")
    return p.parse_args(argv)


def _run(args: argparse.Namespace) -> None:
    _check_config_permissions(args.config)
    cfg = _load_config(args.config)
    base_url = cfg.get("TAIGA_URL", "").rstrip("/")
    username = cfg.get("TAIGA_USERNAME", "")
    password = cfg.get("TAIGA_PASSWORD", "")
    project_slug = args.project or cfg.get("TAIGA_PROJECT_SLUG", "")

    if args.verify:
        token = _authenticate_or_raise(base_url, username, password, args.config)
        project_id = _lookup_project_or_raise(base_url, token, project_slug, args.config)
        print(f"OK — reached Taiga at {base_url}, authenticated as '{username}', "
              f"and found project '{project_slug}' (id {project_id}). No userstory was created.")
        return

    # Missing/empty spec file is checked before any network call at all
    # (docs/spec.md acceptance criteria) -- applies to both the normal push
    # path and --dry-run (which still needs real subject/description text to
    # preview), just not --verify (handled above, returns before this point).
    if not os.path.isfile(args.spec) or os.path.getsize(args.spec) == 0:
        raise TaigaPushError(_no_spec_msg(args.spec))
    with open(args.spec, "r") as f:
        spec_text = f.read()
    if not spec_text.strip():
        raise TaigaPushError(_no_spec_msg(args.spec))

    subject, description = _build_subject_and_description(spec_text, args.spec)

    token = _authenticate_or_raise(base_url, username, password, args.config)
    project_id = _lookup_project_or_raise(base_url, token, project_slug, args.config)

    if args.dry_run:
        print("Dry run — no request was sent. Would create this userstory:")
        print(f"Project: {project_slug} (id {project_id})")
        print(f"Subject: {subject}")
        print("Description:")
        print(description)
        return

    result = _create_userstory_or_raise(base_url, token, project_id, subject, description,
                                         args.config)
    ref = result.get("ref")
    url = f"{base_url}/project/{project_slug}/us/{ref}"
    print(f"Created userstory #{ref}: {url}")


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        _run(args)
        return 0
    except TaigaPushError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
