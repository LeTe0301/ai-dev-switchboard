#!/usr/bin/env python3
"""
Tests for install.sh's item-44 fix (docs/BACKLOG.md item 44,
docs/spec.md "Item 44 -- taiga-gateway's host port never actually gets
published"): Compose merges list fields like `ports:` by *concatenation*,
not by replacement, so a docker-compose.override.yml `ports:` entry for
taiga-gateway never actually closed the pinned taiga-docker checkout's own
unrestricted `"9000:80"` binding -- it just added a second, losing bind,
leaving taiga-gateway running with no port published at all. The fix
patches the pinned checkout's own docker-compose.yml `ports:` line
directly, in place, via a narrowly-scoped, idempotent, grep-gated `sed`.

Technique: extracts install.sh's real item-44 patch step verbatim out of
its own source (same `_extract_between()`-style harness
tests/test_install_code_server_path.py already establishes), then runs it
as a standalone bash script against a scratch $TAIGA_DIR seeded with the
*real* upstream taiga-docker `stable` branch docker-compose.yml content
(fetched verbatim from
https://raw.githubusercontent.com/taigaio/taiga-docker/stable/docker-compose.yml
on 2026-08-16, during this fix's development -- confirms the sed/grep
patterns actually match real upstream content, not a guessed-at
approximation) -- no real Docker/network needed.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_taiga_gateway_port.py
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

# Verbatim (whitespace-for-whitespace) copy of
# https://raw.githubusercontent.com/taigaio/taiga-docker/stable/docker-compose.yml
# as fetched 2026-08-16 -- see docs/implementation.md for how this was
# confirmed against the real upstream file rather than guessed at.
UPSTREAM_DOCKER_COMPOSE_YML = """\
version: "3.5"

x-environment:
  &default-back-environment
  # These environment variables will be used by taiga-back and taiga-async.
  # Database settings
  POSTGRES_DB: "taiga"
  POSTGRES_USER: "${POSTGRES_USER}"
  POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
  POSTGRES_HOST: "taiga-db"
  # Taiga settings
  TAIGA_SECRET_KEY: "${SECRET_KEY}"
  TAIGA_SITES_SCHEME: "${TAIGA_SCHEME}"
  TAIGA_SITES_DOMAIN: "${TAIGA_DOMAIN}"
  TAIGA_SUBPATH: "${SUBPATH}"
  # Email settings.
  EMAIL_BACKEND: "django.core.mail.backends.${EMAIL_BACKEND}.EmailBackend"
  DEFAULT_FROM_EMAIL: "${EMAIL_DEFAULT_FROM}"
  EMAIL_USE_TLS: "${EMAIL_USE_TLS}"
  EMAIL_USE_SSL: "${EMAIL_USE_SSL}"
  EMAIL_HOST: "${EMAIL_HOST}"
  EMAIL_PORT: "${EMAIL_PORT}"
  EMAIL_HOST_USER: "${EMAIL_HOST_USER}"
  EMAIL_HOST_PASSWORD: "${EMAIL_HOST_PASSWORD}"
  # Rabbitmq settings
  RABBITMQ_USER: "${RABBITMQ_USER}"
  RABBITMQ_PASS: "${RABBITMQ_PASS}"
  # Telemetry settings
  ENABLE_TELEMETRY: "${ENABLE_TELEMETRY}"
  # ...your customizations go here

x-volumes:
  &default-back-volumes
  # These volumens will be used by taiga-back and taiga-async.
  - taiga-static-data:/taiga-back/static
  - taiga-media-data:/taiga-back/media
  # - ./config.py:/taiga-back/settings/config.py

services:
  taiga-db:
    image: postgres:12.3
    environment:
      POSTGRES_DB: "taiga"
      POSTGRES_USER: "${POSTGRES_USER}"
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 2s
      timeout: 15s
      retries: 5
      start_period: 3s
    volumes:
      - taiga-db-data:/var/lib/postgresql/data
    networks:
      - taiga

  taiga-back:
    image: taigaio/taiga-back:latest
    environment: *default-back-environment
    volumes: *default-back-volumes
    networks:
      - taiga
    depends_on:
      taiga-db:
        condition: service_healthy
      taiga-events-rabbitmq:
        condition: service_started
      taiga-async-rabbitmq:
        condition: service_started

  taiga-async:
    image: taigaio/taiga-back:latest
    entrypoint: ["/taiga-back/docker/async_entrypoint.sh"]
    environment: *default-back-environment
    volumes: *default-back-volumes
    networks:
      - taiga
    depends_on:
      taiga-db:
        condition: service_healthy
      taiga-events-rabbitmq:
        condition: service_started
      taiga-async-rabbitmq:
        condition: service_started

  taiga-async-rabbitmq:
    image: rabbitmq:3.8-management-alpine
    environment:
      RABBITMQ_ERLANG_COOKIE: "${RABBITMQ_ERLANG_COOKIE}"
      RABBITMQ_DEFAULT_USER: "${RABBITMQ_USER}"
      RABBITMQ_DEFAULT_PASS: "${RABBITMQ_PASS}"
      RABBITMQ_DEFAULT_VHOST: "${RABBITMQ_VHOST}"
    hostname: "taiga-async-rabbitmq"
    volumes:
      - taiga-async-rabbitmq-data:/var/lib/rabbitmq
    networks:
      - taiga

  taiga-front:
    image: taigaio/taiga-front:latest
    environment:
      TAIGA_URL: "${TAIGA_SCHEME}://${TAIGA_DOMAIN}"
      TAIGA_WEBSOCKETS_URL: "${WEBSOCKETS_SCHEME}://${TAIGA_DOMAIN}"
      TAIGA_SUBPATH: "${SUBPATH}"
      # ...your customizations go here
    networks:
      - taiga
    # volumes:
    #   - ./conf.json:/usr/share/nginx/html/conf.json

  taiga-events:
    image: taigaio/taiga-events:latest
    environment:
      RABBITMQ_USER: "${RABBITMQ_USER}"
      RABBITMQ_PASS: "${RABBITMQ_PASS}"
      TAIGA_SECRET_KEY: "${SECRET_KEY}"
    networks:
      - taiga
    depends_on:
      taiga-events-rabbitmq:
        condition: service_started

  taiga-events-rabbitmq:
    image: rabbitmq:3.8-management-alpine
    environment:
      RABBITMQ_ERLANG_COOKIE: "${RABBITMQ_ERLANG_COOKIE}"
      RABBITMQ_DEFAULT_USER: "${RABBITMQ_USER}"
      RABBITMQ_DEFAULT_PASS: "${RABBITMQ_PASS}"
      RABBITMQ_DEFAULT_VHOST: "${RABBITMQ_VHOST}"
    hostname: "taiga-events-rabbitmq"
    volumes:
      - taiga-events-rabbitmq-data:/var/lib/rabbitmq
    networks:
      - taiga

  taiga-protected:
    image: taigaio/taiga-protected:latest
    environment:
      MAX_AGE: "${ATTACHMENTS_MAX_AGE}"
      SECRET_KEY: "${SECRET_KEY}"
    networks:
      - taiga

  taiga-gateway:
    image: nginx:1.19-alpine
    ports:
      - "9000:80"
    volumes:
      - ./taiga-gateway/taiga.conf:/etc/nginx/conf.d/default.conf
      - taiga-static-data:/taiga/static
      - taiga-media-data:/taiga/media
    networks:
      - taiga
    depends_on:
      - taiga-front
      - taiga-back
      - taiga-events

volumes:
  taiga-static-data:
  taiga-media-data:
  taiga-db-data:
  taiga-async-rabbitmq-data:
  taiga-events-rabbitmq-data:

networks:
  taiga:
"""

PATCHED_TAIGA_GATEWAY_PORTS_LINE = '      - "127.0.0.1:${TAIGA_PORT}:80"'


def _extract_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _extract_patch_block():
    with open(INSTALL_SH) as f:
        source = f.read()
    return _extract_between(
        source, "# 3. Item 44 (round 9):", "# 4. Config —")


class TaigaGatewayPortPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-taiga-gateway-port-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.taiga_dir = os.path.join(self.tmp, "taiga-docker")
        os.makedirs(self.taiga_dir)
        self.compose_yml = os.path.join(self.taiga_dir, "docker-compose.yml")
        self.patch_block = _extract_patch_block()

    def _write_compose_yml(self, content):
        with open(self.compose_yml, "w") as f:
            f.write(content)

    def _read_compose_yml(self):
        with open(self.compose_yml) as f:
            return f.read()

    def _run_patch(self, timeout=10):
        """Runs the real extracted item-44 patch step against
        self.taiga_dir/docker-compose.yml, with TAIGA_DIR pointed at the
        scratch directory -- exactly what install.sh does at real install
        time, minus everything else in the --with-taiga block."""
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            TAIGA_DIR={self.taiga_dir!r}
            {self.patch_block}
            """)
        return subprocess.run(["bash", "-c", script], capture_output=True,
                               text=True, timeout=timeout)

    def test_fresh_unpatched_checkout_gets_port_patched_to_loopback_only(self):
        # The exact real-world repro: a brand-new clone of upstream's real
        # docker-compose.yml, unpatched ports: "9000:80" line.
        self._write_compose_yml(UPSTREAM_DOCKER_COMPOSE_YML)
        result = self._run_patch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("WARNING", result.stderr)
        content = self._read_compose_yml()
        self.assertIn(PATCHED_TAIGA_GATEWAY_PORTS_LINE, content)
        self.assertNotIn('- "9000:80"', content)

    def test_only_the_ports_line_changes_rest_of_file_untouched(self):
        # Narrow-scope guarantee (docs/spec.md "Proposed approach" -- exactly
        # one line, one file): every other line of the real upstream file,
        # including taiga.conf's own volumes: bind mount (item 43's
        # mechanism lives entirely in the override file, not here) must
        # survive byte-for-byte.
        self._write_compose_yml(UPSTREAM_DOCKER_COMPOSE_YML)
        self._run_patch()
        content = self._read_compose_yml()
        before_lines = UPSTREAM_DOCKER_COMPOSE_YML.splitlines()
        after_lines = content.splitlines()
        self.assertEqual(len(before_lines), len(after_lines))
        diffs = [
            (i, b, a) for i, (b, a) in enumerate(zip(before_lines, after_lines))
            if b != a
        ]
        self.assertEqual(len(diffs), 1, diffs)
        _, before_line, after_line = diffs[0]
        self.assertEqual(before_line.strip(), '- "9000:80"')
        self.assertEqual(after_line, PATCHED_TAIGA_GATEWAY_PORTS_LINE)

    def test_rerun_against_preexisting_unpatched_install_still_gets_patched(self):
        # docs/spec.md Edge cases: "Re-run/upgrade of an existing install
        # that predates this fix" -- the step is not gated behind
        # TAIGA_FRESH_CLONE, so an existing, never-git-pulled checkout with
        # the original unpatched line must still get patched on its next
        # install.sh run. Simulated here simply by *not* setting
        # TAIGA_FRESH_CLONE at all before running the block, proving it
        # doesn't branch on that variable.
        self._write_compose_yml(UPSTREAM_DOCKER_COMPOSE_YML)
        result = self._run_patch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(PATCHED_TAIGA_GATEWAY_PORTS_LINE, self._read_compose_yml())

    def test_rerun_against_already_patched_checkout_is_byte_identical_noop(self):
        # Idempotency: running the block a second (and third) time against
        # an already-patched file must not double-patch or corrupt it.
        self._write_compose_yml(UPSTREAM_DOCKER_COMPOSE_YML)
        first = self._run_patch()
        self.assertEqual(first.returncode, 0, first.stderr)
        after_first = self._read_compose_yml()

        second = self._run_patch()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("WARNING", second.stderr)
        after_second = self._read_compose_yml()
        self.assertEqual(after_first, after_second)

        third = self._run_patch()
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertNotIn("WARNING", third.stderr)
        self.assertEqual(after_first, self._read_compose_yml())

    def test_unexpected_format_warns_loudly_and_leaves_file_untouched(self):
        # docs/spec.md Edge cases: a future upstream format change (or a
        # manually-edited checkout) that matches neither the original nor
        # the already-patched form must warn loudly to stderr, not silently
        # leave the port unpatched, and must not fail the install.
        mismatched = "services:\n  taiga-gateway:\n    ports:\n      - \"9001:81\"\n"
        self._write_compose_yml(mismatched)
        result = self._run_patch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING", result.stderr)
        self.assertIn("taiga-gateway", result.stderr)
        # File is genuinely untouched, not just "still doesn't match" --
        # the warn branch must not fall through into some other edit.
        self.assertEqual(self._read_compose_yml(), mismatched)

    def test_already_patched_form_present_from_the_start_is_a_clean_noop(self):
        # Same warn-vs-noop distinction, but for the case where the file
        # already contains exactly the patched form (e.g. a checkout that
        # was patched by a previous install.sh version, or hand-edited to
        # match) -- must be recognized as already-done, not warned about.
        pre_patched = (
            "services:\n  taiga-gateway:\n    ports:\n"
            f"{PATCHED_TAIGA_GATEWAY_PORTS_LINE}\n"
        )
        self._write_compose_yml(pre_patched)
        result = self._run_patch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("WARNING", result.stderr)
        self.assertEqual(self._read_compose_yml(), pre_patched)


if __name__ == "__main__":
    unittest.main()
