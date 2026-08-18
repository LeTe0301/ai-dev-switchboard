"""Refuse to run this suite as root.

Two things go wrong when these tests run as root, both observed for real:

1. **Some tests touch real paths.** Parts of this suite have historically
   operated on the live `/etc/ai-dev-switchboard` config rather than a
   temp copy -- as root that is destructive, and it has already destroyed
   a live config once. As an unprivileged user the same test fails
   harmlessly with EPERM instead.

2. **Root leaves debris that breaks the repo for its owner.** pytest
   writes `__pycache__/` and `.pytest_cache/` next to the sources it
   imports. Run as root inside a `dev`-owned checkout, those land
   root-owned, and from then on `dev` cannot `git checkout`/`merge`
   across them ("unable to unlink ... Permission denied") until someone
   chowns them back. Found on 2026-08-18 across three project checkouts,
   including a root-owned `.git/index`.

Override deliberately, per-invocation, if you really mean it:

    AI_DEV_SWITCHBOARD_ALLOW_ROOT_TESTS=1 python3 -m pytest tests/

Note this guard is pytest-only. `python3 -m unittest` does not load
conftest.py, so that path is still unguarded.
"""
import os

import pytest

_OVERRIDE = "AI_DEV_SWITCHBOARD_ALLOW_ROOT_TESTS"


def pytest_configure(config):
    if hasattr(os, "geteuid") and os.geteuid() == 0 and not os.environ.get(_OVERRIDE):
        raise pytest.UsageError(
            "Refusing to run the test suite as root.\n"
            "\n"
            "  * some tests operate on real paths (the live "
            "/etc/ai-dev-switchboard config among them) and as root that is\n"
            "    destructive rather than a harmless permission error;\n"
            "  * pytest's own __pycache__/.pytest_cache would be written "
            "root-owned into this checkout, after which the repo's owner\n"
            "    can no longer switch branches across those paths.\n"
            "\n"
            "Re-run as the checkout's owner (e.g. `sudo -u dev python3 -m "
            "pytest tests/`),\n"
            "or set %s=1 if you genuinely intend to run as root." % _OVERRIDE
        )
