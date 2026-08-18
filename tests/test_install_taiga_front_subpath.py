#!/usr/bin/env python3
"""
Tests for install.sh's backlog item 47(b) fix (docs/spec.md "Part B — Item
47: Taiga frontend under a tailscale-serve subpath"): taiga-front's own
`docker-compose.yml` `environment:` block (in the cloned taiga-docker
checkout, `$TAIGA_DIR/docker-compose.yml`) reads `TAIGA_URL`/
`TAIGA_WEBSOCKETS_URL`/`TAIGA_SUBPATH` from `${TAIGA_SCHEME}`/
`${TAIGA_DOMAIN}`/`${WEBSOCKETS_SCHEME}`/`${SUBPATH}` -- all via the same
root `$TAIGA_DIR/.env` Compose already auto-loads for taiga-back/taiga-
gateway (confirmed against the real cloned checkout, not a separate `taiga-
front/.env` file). `TAIGA_SCHEME`/`TAIGA_DOMAIN` already reached taiga-front
for free through that shared file; `SUBPATH`/`WEBSOCKETS_SCHEME` were never
written anywhere. The fix adds both, following the same PUBLISH_MODE/
BASE_URL conditional `TAIGA_DOMAIN` already uses (tailscale+BASE_URL set ->
derive; else -> plain local values, matching app.py's own
`_taiga_display_url()`, which only prefixes `/taiga` under
PUBLISH_MODE=tailscale).

Technique: extracts install.sh's real fix block verbatim out of its own
source (same `_extract_between()`-style harness tests/test_install_set_env.py
and tests/test_install_taiga_gateway_port.py already establish) and runs it
as a standalone bash script against a scratch $TAIGA_ENV file -- pure
set_env/get_env logic, no Docker/network needed.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_taiga_front_subpath.py
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")


def _extract_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _extract_set_env_and_get_env():
    with open(INSTALL_SH) as f:
        source = f.read()
    return _extract_between(source, "set_env() {", "random_token() {")


def _extract_subpath_block():
    with open(INSTALL_SH) as f:
        source = f.read()
    return _extract_between(
        source,
        "# 4b. taiga-front's own SUBPATH/WEBSOCKETS_SCHEME (backlog item 47b).",
        "# TAIGA_PORT also lives in taiga-docker's own .env",
    )


class TaigaFrontSubpathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-taiga-front-subpath-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.taiga_env = os.path.join(self.tmp, ".env")
        open(self.taiga_env, "w").close()
        self.helpers = _extract_set_env_and_get_env()
        self.block = _extract_subpath_block()

    def _run(self, publish_mode, base_url, taiga_port=9000, timeout=10):
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            {self.helpers}
            TAIGA_ENV={self.taiga_env!r}
            TAIGA_PORT={taiga_port}
            PUBLISH_MODE={publish_mode!r}
            BASE_URL={base_url!r}
            {self.block}
            """)
        return subprocess.run(["bash", "-c", script], capture_output=True,
                               text=True, timeout=timeout)

    def _read_env(self):
        with open(self.taiga_env) as f:
            return dict(
                line.rstrip("\n").split("=", 1)
                for line in f if line.strip()
            )

    # ── tailscale + BASE_URL set -> /taiga subpath, wss websockets ───────

    def test_tailscale_with_base_url_sets_subpath_and_wss(self):
        result = self._run(publish_mode="tailscale",
                            base_url="https://host.ts.net")
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self._read_env()
        self.assertEqual(env["SUBPATH"], "/taiga")
        self.assertEqual(env["WEBSOCKETS_SCHEME"], "wss")

    # ── PUBLISH_MODE=none -> no subpath prefix, plain ws ──────────────────

    def test_publish_mode_none_leaves_subpath_empty_and_uses_ws(self):
        result = self._run(publish_mode="none", base_url="")
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self._read_env()
        self.assertEqual(env["SUBPATH"], "")
        self.assertEqual(env["WEBSOCKETS_SCHEME"], "ws")

    # ── tailscale mode but BASE_URL not yet configured -> same as local ──

    def test_tailscale_without_base_url_falls_back_to_local(self):
        result = self._run(publish_mode="tailscale", base_url="")
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self._read_env()
        self.assertEqual(env["SUBPATH"], "")
        self.assertEqual(env["WEBSOCKETS_SCHEME"], "ws")

    # ── re-derived every run (not "only if empty"), like TAIGA_DOMAIN ────

    def test_switching_publish_mode_between_runs_updates_existing_values(self):
        first = self._run(publish_mode="tailscale",
                           base_url="https://host.ts.net")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self._read_env()["SUBPATH"], "/taiga")

        second = self._run(publish_mode="none", base_url="")
        self.assertEqual(second.returncode, 0, second.stderr)
        env = self._read_env()
        self.assertEqual(env["SUBPATH"], "")
        self.assertEqual(env["WEBSOCKETS_SCHEME"], "ws")
        # No accumulation -- exactly one line per key.
        with open(self.taiga_env) as f:
            content = f.read()
        self.assertEqual(content.count("SUBPATH="), 1)
        self.assertEqual(content.count("WEBSOCKETS_SCHEME="), 1)

    # ── matches the live-confirmed CT110 values from docs/BACKLOG.md
    #    item 47 byte-for-byte (SUBPATH="/taiga", WEBSOCKETS_SCHEME=wss
    #    under tailscale+BASE_URL) ────────────────────────────────────────

    def test_matches_live_confirmed_ct110_values_under_tailscale(self):
        result = self._run(publish_mode="tailscale",
                            base_url="https://dev.tailbe22cd.ts.net")
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self._read_env()
        self.assertEqual(env["SUBPATH"], "/taiga")
        self.assertEqual(env["WEBSOCKETS_SCHEME"], "wss")


if __name__ == "__main__":
    unittest.main()
