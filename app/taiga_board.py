#!/usr/bin/env python3
"""
Taiga kanban board client for the lead's board_read/board_write tools
(backlog item 7 part 1 -- docs/spec.md). Importable module, not a CLI
script -- called synchronously from app/teams.py's team_step()/
resolve_board_write(), mid-request-thread, and (part 2) from app/app.py's
own web route.

Same urllib.request-only, stdlib-only pattern as scripts/taiga_push_spec.py
(a fresh auth token per call, no caching) -- the exception shapes
(TaigaPushError/TaigaConnectionError/TaigaHTTPError) and the single shared
_taiga_request() seam are copied, not imported, from that script
(docs/spec.md "Proposed approach" §1): that script is deliberately
self-contained/stdlib-only and lives under scripts/, not app/, so
app/teams.py importing it directly would need a sys.path reach out of
app/ -- and docs/spec.md's own Non-goals explicitly rule out refactoring
that script into a shared dependency. This module reuses
~/.config/ai-dev-switchboard/taiga-push.env byte-for-byte (same four keys,
same scripts/taiga-configure-push.sh setup step) -- no new credentials
file.

Run with:
    python3 -m unittest discover -s tests -v
"""
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/ai-dev-switchboard/taiga-push.env")


class TaigaPushError(Exception):
    """Raised for any failure app/teams.py's board_read/board_write branches
    and resolve_board_write() catch and fold into an ordinary round outcome
    (docs/spec.md "board_read... catching TaigaPushError subclasses and
    turning them into an ordinary round outcome") -- never left to
    propagate as an unhandled exception. str(e) is always a clear,
    human-readable message safe to show the lead."""


class TaigaConnectionError(TaigaPushError):
    """Taiga couldn't be reached at all (connection refused, DNS failure,
    timeout, malformed URL, ...) -- distinct from a non-2xx HTTP response,
    same split scripts/taiga_push_spec.py already makes."""


class TaigaHTTPError(TaigaPushError):
    """Taiga responded with a non-2xx status. Callers inspect .status to
    decide which of this module's own edge-case messages applies (see
    lookup_project()/get_userstory()/set_status() etc.) -- str(e) alone is
    also always a safe, generic fallback."""

    def __init__(self, status: int, body: str = ""):
        self.status = status
        self.body = body
        super().__init__(f"Taiga returned HTTP {status}")


def _taiga_request(base_url: str, method: str, path: str, token: str = None, body: dict = None):
    """
    One shared urllib.request wrapper -- the single seam
    tests/test_teams_board.py monkeypatches (mirrors scripts/
    taiga_push_spec.py's own _taiga_request(), byte-for-byte identical
    behavior: raises TaigaHTTPError on any non-2xx status, TaigaConnection
    Error on a connection failure OR a malformed base_url/Request()
    construction failure (docs/test-review.md Defect 1's fix, carried
    forward here since this module makes the identical urllib.request.
    Request(...) call). Never includes the raw Taiga response body in a
    message that could propagate to the lead -- see TaigaHTTPError.body,
    which callers must not surface verbatim.
    """
    url = f"{base_url}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
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


def _check_config_permissions(path: str) -> None:
    """Same loud-warning-not-hard-block convention as scripts/
    taiga_push_spec.py's own _check_config_permissions() -- this reads the
    identical credentials file, so the identical warning applies here too."""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return
    if mode & 0o077:
        print(
            f"WARNING: {path} is readable by group/other (mode {oct(mode)}) — it holds a "
            f"live Taiga password. Run: chmod 600 {path}",
            file=sys.stderr,
        )


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Same KEY=value parse as scripts/taiga_push_spec.py's own
    _load_config() (docs/spec.md "Taiga credentials and project
    resolution"). Raises TaigaPushError with a clear, actionable message
    (never KeyError, never a bare traceback) if the file is missing --
    board_read/board_write's own "Taiga isn't configured" edge case."""
    if not os.path.isfile(path):
        raise TaigaPushError(
            f"Taiga isn't configured — run scripts/taiga-configure-push.sh first "
            f"(expected config at {path}).")
    _check_config_permissions(path)
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
            f"Taiga isn't configured — run scripts/taiga-configure-push.sh first "
            f"(expected config at {path}).")
    return cfg


def authenticate(base_url: str, username: str, password: str) -> str:
    """POST /api/v1/auth -> bearer auth_token. A fresh token is exchanged on
    every call (docs/spec.md "Proposed approach" §1: "a fresh token per
    call, no caching"), same as scripts/taiga_push_spec.py's own
    _authenticate()."""
    try:
        data = _taiga_request(base_url, "POST", "/api/v1/auth",
                              body={"type": "normal", "username": username, "password": password})
    except TaigaConnectionError:
        raise TaigaConnectionError(
            f"Could not reach Taiga at {base_url} — make sure it's toggled on in the "
            "ai-dev-switchboard web UI, or check TAIGA_URL in the Taiga push config.")
    except TaigaHTTPError:
        raise TaigaPushError(
            "Taiga rejected the configured username/password — check TAIGA_USERNAME/"
            "TAIGA_PASSWORD, or re-run scripts/taiga-configure-push.sh.")
    token = data.get("auth_token")
    if not token:
        raise TaigaPushError(
            "Taiga rejected the configured username/password — check TAIGA_USERNAME/"
            "TAIGA_PASSWORD, or re-run scripts/taiga-configure-push.sh.")
    return token


def lookup_project(base_url: str, token: str, slug: str) -> int:
    """GET /api/v1/projects/by_slug?slug=<slug> -> numeric project id, same
    endpoint scripts/taiga_push_spec.py's own _lookup_project() already
    uses."""
    try:
        data = _taiga_request(base_url, "GET",
                              f"/api/v1/projects/by_slug?slug={urllib.parse.quote(slug)}",
                              token=token)
    except TaigaHTTPError as e:
        if e.status == 404:
            raise TaigaPushError(f"no Taiga project found with slug '{slug}'.")
        if e.status in (401, 403):
            raise TaigaPushError(
                "Taiga rejected the configured username/password — check TAIGA_USERNAME/"
                "TAIGA_PASSWORD, or re-run scripts/taiga-configure-push.sh.")
        raise TaigaPushError(f"Taiga rejected the project lookup (HTTP {e.status}).")
    return data["id"]


def resolve_session(config_path: str = DEFAULT_CONFIG_PATH):
    """
    Shared glue for every board_read/board_write call from app/teams.py:
    load_config() -> authenticate() -> lookup_project(), the same three
    steps scripts/taiga_push_spec.py's own _run() already performs in this
    order (docs/spec.md "Taiga credentials and project resolution" --
    reuses 1b's already-shipped one-shared-TAIGA_PROJECT_SLUG default).
    Not one of the eight functions docs/spec.md's own "Proposed approach"
    code block names explicitly, but a small, private-in-spirit wiring
    helper around them -- see docs/implementation.md "Key decisions" for
    why this is a plain function call, not a cached/global session (no
    caching anywhere in this module, matching authenticate()'s own "fresh
    token per call" discipline).

    Returns (base_url, token, project_id). Raises TaigaPushError (config
    missing) or TaigaConnectionError/TaigaHTTPError-derived TaigaPushError
    on an auth/project-lookup failure -- every failure here is already one
    of this module's own three exception types, so a caller catching
    TaigaPushError catches everything this function can raise.
    """
    cfg = load_config(config_path)
    base_url = (cfg.get("TAIGA_URL") or "").rstrip("/")
    username = cfg.get("TAIGA_USERNAME", "")
    password = cfg.get("TAIGA_PASSWORD", "")
    slug = cfg.get("TAIGA_PROJECT_SLUG", "")
    token = authenticate(base_url, username, password)
    project_id = lookup_project(base_url, token, slug)
    return base_url, token, project_id


def _normalize_userstory(data: dict) -> dict:
    """Shared shape for get_userstory()/list_userstories()'s own entries
    (docs/spec.md "status_id for set_status": "list_userstories/
    get_userstory should surface each userstory's status as both its
    numeric id and its human-readable name")."""
    status_extra = data.get("status_extra_info") or {}
    return {
        "id": data.get("id"),
        "ref": data.get("ref"),
        "subject": data.get("subject") or "",
        "description": data.get("description") or "",
        "version": data.get("version"),
        "status_id": data.get("status"),
        "status_name": status_extra.get("name") or "",
    }


def get_userstory(base_url: str, token: str, project_id: int, ref: int) -> dict:
    """GET /api/v1/userstories/by_ref?ref=<ref>&project=<project_id> -> one
    normalized userstory dict (see _normalize_userstory()). A 404 (no such
    ref on this board) is surfaced as a clear TaigaPushError, docs/spec.md
    "Edge cases" own "ref doesn't exist on the board" case -- never a
    proposal queued, never an unhandled exception."""
    try:
        data = _taiga_request(base_url, "GET",
                              f"/api/v1/userstories/by_ref?ref={ref}&project={project_id}",
                              token=token)
    except TaigaHTTPError as e:
        if e.status == 404:
            raise TaigaPushError(f"no such userstory on this board: ref {ref}.")
        raise TaigaPushError(f"Taiga rejected the userstory lookup (HTTP {e.status}).")
    return _normalize_userstory(data)


def list_userstories(base_url: str, token: str, project_id: int, query: str = None,
                     limit: int = 10) -> list:
    """
    GET /api/v1/userstories?project=<project_id> -> every userstory on the
    board, then bounded client-side (docs/spec.md Goals: "one specific card
    by ref... or a bounded list matching a substring query, or (no args)
    the most recently modified cards"): `query` given -> case-insensitive
    substring match against `subject`; no query -> sorted by
    `modified_date` descending. Either way, capped to `limit` entries --
    "bounded" is the operative word, this never returns an unbounded board
    dump to the lead's own prompt.
    """
    try:
        data = _taiga_request(base_url, "GET", f"/api/v1/userstories?project={project_id}",
                              token=token)
    except TaigaHTTPError as e:
        raise TaigaPushError(f"Taiga rejected the userstory list (HTTP {e.status}).")
    if not isinstance(data, list):
        data = []
    if query:
        q = query.lower()
        data = [us for us in data if q in (us.get("subject") or "").lower()]
    else:
        data = sorted(data, key=lambda us: us.get("modified_date") or "", reverse=True)
    return [_normalize_userstory(us) for us in data[:limit]]


def list_userstory_statuses(base_url: str, token: str, project_id: int) -> list:
    """
    GET /api/v1/userstory-statuses?project=<project_id> -> the project's own
    configured Kanban columns, each {"id", "name"}. Used only by
    resolve_board_write()'s own set_status apply step, to resolve
    board_write's human-readable `value` (e.g. "In progress", matching the
    acceptance criteria's own example) to the numeric status id set_status()
    itself requires -- docs/spec.md "status_id for set_status" explicitly
    says "the apply step uses the id" while the proposal itself stays
    human-readable; this is the lookup that makes that translation
    possible. Not one of the eight functions docs/spec.md's own "Proposed
    approach" code block names explicitly -- see docs/implementation.md
    "Deviations from spec".
    """
    try:
        data = _taiga_request(base_url, "GET",
                              f"/api/v1/userstory-statuses?project={project_id}", token=token)
    except TaigaHTTPError as e:
        raise TaigaPushError(f"Taiga rejected the status list (HTTP {e.status}).")
    if not isinstance(data, list):
        data = []
    return [{"id": s.get("id"), "name": s.get("name") or ""} for s in data]


def _patch_userstory_or_raise(base_url: str, token: str, us_id: int, body: dict) -> dict:
    """Shared PATCH wrapper for set_status()/amend_description()/
    append_comment() -- one call site for the optimistic-concurrency error
    translation docs/spec.md's own "Open questions" flags as an
    implementation detail to confirm against a live instance: a stale
    `version` is modeled here as a 400/409 rejection (Taiga's documented
    version-field convention), surfaced as a clear conflict message rather
    than the generic "Taiga rejected the write" fallback."""
    try:
        return _taiga_request(base_url, "PATCH", f"/api/v1/userstories/{us_id}", token=token, body=body)
    except TaigaHTTPError as e:
        if e.status == 404:
            raise TaigaPushError(f"no such userstory: id {us_id} (it may have been deleted).")
        if e.status in (400, 409):
            raise TaigaPushError(
                "Taiga rejected the write -- this card was likely changed concurrently "
                f"(version conflict, HTTP {e.status}).")
        raise TaigaPushError(f"Taiga rejected the write (HTTP {e.status}).")


def set_status(base_url: str, token: str, us_id: int, version: int, status_id: int) -> dict:
    """PATCH /api/v1/userstories/{id} {"version", "status"} -- moves the
    card between Kanban columns (docs/spec.md "Verb set naming": Taiga's
    columns ARE a userstory's status values). `version` must be freshly
    fetched (get_userstory()) immediately before this call, never a
    proposal-time snapshot -- see resolve_board_write()."""
    data = _patch_userstory_or_raise(base_url, token, us_id, {"version": version, "status": status_id})
    return _normalize_userstory(data)


def amend_description(base_url: str, token: str, us_id: int, version: int, description: str) -> dict:
    """PATCH /api/v1/userstories/{id} {"version", "description"}."""
    data = _patch_userstory_or_raise(base_url, token, us_id,
                                     {"version": version, "description": description})
    return _normalize_userstory(data)


def append_comment(base_url: str, token: str, us_id: int, version: int, comment: str) -> dict:
    """PATCH /api/v1/userstories/{id} {"version", "comment"} -- Taiga's
    documented comment-via-PATCH convention for this resource type (docs/
    spec.md "Open questions": "Comment posting mechanics" -- flagged there
    as an implementation detail to adjust if a live instance needs a
    distinct endpoint/shape; this is the modeled default)."""
    data = _patch_userstory_or_raise(base_url, token, us_id, {"version": version, "comment": comment})
    return _normalize_userstory(data)
