#!/usr/bin/env python3
"""
Tests for install.sh's backlog item 48 fix (docs/spec.md "Part A — Item 48:
Gitea under a subpath keeps stale links after ROOT_URL/DOMAIN change"):
Gitea's official Docker image only applies GITEA__server__ROOT_URL/DOMAIN to
the persisted app.ini on a container's *first ever* start, so install.sh
re-writing those values into $GITEA_DIR/.env on a later run was previously a
no-op against an already-created `server` container (the service name in
config/gitea-docker-compose.yml -- NOT `gitea`). The fix captures the
previous values via get_env before overwriting them, and force-recreates
`server` (only, not `db`) when they actually changed and the container
already exists.

`--no-deps` (docs/test-review.md Defect 2, reviewer-found post-approval
regression): `server` and `db` both load the same `env_file: - .env`, so a
changed `.env` also changes Compose's own computed config hash for `db`,
not just `server` -- and because `server` has `depends_on: - db`, naming
only `server` on the command line still pulled `db` into scope to satisfy
the dependency, so real Compose recreated `db` too even though
`--force-recreate` was only given for `server`. `--no-deps` keeps `db` out
of scope entirely. This is exactly the kind of defect a mocked-`docker`
test (see `GiteaForceRecreateTests` below) cannot catch on its own -- the
mock only proves the *command string* is correct, not real Compose's own
dependency-scope-inclusion + config-hash-diffing behavior -- so
`GiteaForceRecreateRealDockerTests` (real `docker compose`, gated on
`HAVE_DOCKER`) exists specifically to close that gap.

Technique: extracts install.sh's real fix block verbatim out of its own
source (same `_extract_between()`-style harness tests/test_install_set_env.py
and tests/test_install_taiga_gateway_port.py already establish), then runs
it as a standalone bash script.
`GiteaForceRecreateTests` uses `docker` on PATH replaced by a small fake
that records every invocation and lets each test control whether the
`server` container "exists" and whether the recreate command "succeeds" --
no real Docker/network needed. `GiteaForceRecreateRealDockerTests` (gated
on `HAVE_DOCKER`, same `unittest.skipUnless` idiom
`tests/test_gitea_sync_project.py` already uses for `HAVE_GIT`) instead runs
the block against a real, isolated Gitea+Postgres compose stack (own scratch
directory, own unique container names, no contact with any live
`ai-dev-switchboard-gitea*` containers) using the real `config/gitea-docker-
compose.yml`.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_gitea_recreate.py
"""
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")
GITEA_COMPOSE_YML = os.path.join(REPO_ROOT, "config",
                                  "gitea-docker-compose.yml")

HAVE_DOCKER = shutil.which("docker") is not None

FAKE_DOCKER = """\
#!/usr/bin/env bash
# Records every invocation to $DOCKER_LOG (one line per call, args only).
# `compose ps -q server` prints a fake container id iff $CONTAINER_EXISTS=1,
# else prints nothing (both exit 0 -- real `docker compose ps -q` never
# fails just because nothing matched). `compose up -d --force-recreate
# server` exits 1 iff $RECREATE_FAILS=1, else exits 0.
echo "$*" >> "$DOCKER_LOG"
if [ "$1" = "compose" ] && [ "$2" = "ps" ]; then
    if [ "${CONTAINER_EXISTS:-0}" = "1" ]; then
        echo "fake-gitea-server-container-id"
    fi
    exit 0
elif [ "$1" = "compose" ] && [ "$2" = "up" ]; then
    if [ "${RECREATE_FAILS:-0}" = "1" ]; then
        exit 1
    fi
    exit 0
fi
exit 0
"""


def _extract_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _extract_set_env_and_get_env():
    with open(INSTALL_SH) as f:
        source = f.read()
    return _extract_between(source, "set_env() {", "random_token() {")


def _extract_recreate_block():
    with open(INSTALL_SH) as f:
        source = f.read()
    return _extract_between(
        source,
        "# Captured *before* being overwritten below (backlog item 48)",
        "# GITEA_PORT/GITEA_SSH_PORT also live in this same .env",
    )


class GiteaForceRecreateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aidswb-gitea-recreate-")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

        self.gitea_dir = os.path.join(self.tmp, "gitea")
        os.makedirs(self.gitea_dir)
        self.gitea_env = os.path.join(self.gitea_dir, ".env")
        open(self.gitea_env, "w").close()

        self.fake_bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.fake_bin)
        docker_path = os.path.join(self.fake_bin, "docker")
        with open(docker_path, "w") as f:
            f.write(FAKE_DOCKER)
        os.chmod(docker_path, os.stat(docker_path).st_mode | stat.S_IEXEC
                  | stat.S_IXGRP | stat.S_IXOTH)

        self.docker_log = os.path.join(self.tmp, "docker.log")
        open(self.docker_log, "w").close()

        self.helpers = _extract_set_env_and_get_env()
        self.recreate_block = _extract_recreate_block()

    def _docker_log_lines(self):
        with open(self.docker_log) as f:
            return [line.rstrip("\n") for line in f if line.strip()]

    def _run(self, publish_mode, base_url, container_exists=False,
              recreate_fails=False, prev_domain=None, prev_root_url=None,
              timeout=10):
        """Seeds $GITEA_ENV with prev_domain/prev_root_url (if given, via the
        real set_env -- same "already-persisted from an earlier run" state
        the real block finds), then runs the real extracted recreate block
        with PUBLISH_MODE/BASE_URL/GITEA_URL_PATH/GITEA_PORT/GITEA_DIR/
        GITEA_ENV set exactly like install.sh's own Gitea block sets them."""
        seed = ""
        if prev_domain is not None:
            seed += (f'set_env {self.gitea_env!r} GITEA__server__DOMAIN '
                      f'{prev_domain!r}\n')
        if prev_root_url is not None:
            seed += (f'set_env {self.gitea_env!r} GITEA__server__ROOT_URL '
                      f'{prev_root_url!r}\n')
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            {self.helpers}
            {seed}
            GITEA_DIR={self.gitea_dir!r}
            GITEA_ENV={self.gitea_env!r}
            GITEA_URL_PATH="/gitea"
            GITEA_PORT=3000
            PUBLISH_MODE={publish_mode!r}
            BASE_URL={base_url!r}
            {self.recreate_block}
            """)
        env = dict(os.environ)
        env["PATH"] = self.fake_bin + os.pathsep + env["PATH"]
        env["DOCKER_LOG"] = self.docker_log
        env["CONTAINER_EXISTS"] = "1" if container_exists else "0"
        env["RECREATE_FAILS"] = "1" if recreate_fails else "0"
        return subprocess.run(["bash", "-c", script], capture_output=True,
                               text=True, timeout=timeout, env=env)

    # ── fresh install: nothing persisted yet, container doesn't exist ───

    def test_fresh_install_no_prior_values_no_container_skips_cleanly(self):
        result = self._run(publish_mode="none", base_url="",
                            container_exists=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self._docker_log_lines()
        # `docker compose ps -q server` is still checked (cheap, needed to
        # know whether to recreate) but `up -d --force-recreate` never runs.
        self.assertTrue(any("ps" in line for line in lines), lines)
        self.assertFalse(any("force-recreate" in line for line in lines),
                          lines)

    # ── re-run, nothing changed: never touches the container ────────────

    def test_unchanged_values_never_calls_recreate_even_if_container_exists(self):
        result = self._run(
            publish_mode="none", base_url="", container_exists=True,
            prev_domain="127.0.0.1:3000",
            prev_root_url="http://127.0.0.1:3000")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self._docker_log_lines()
        self.assertFalse(any("force-recreate" in line for line in lines),
                          lines)

    # ── the actual bug: value changed, container already exists ─────────

    def test_changed_domain_and_root_url_force_recreates_server_only(self):
        result = self._run(
            publish_mode="tailscale", base_url="https://host.ts.net",
            container_exists=True,
            prev_domain="127.0.0.1:3000",
            prev_root_url="http://127.0.0.1:3000")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self._docker_log_lines()
        recreate_calls = [line for line in lines if "force-recreate" in line]
        self.assertEqual(len(recreate_calls), 1, lines)
        self.assertIn("up -d --force-recreate --no-deps server",
                       recreate_calls[0])
        # Scoped to `server` only -- never a bare --force-recreate for the
        # whole stack, and `db` is never named. `--no-deps` keeps `db` out
        # of Compose's own dependency-resolution scope entirely (see
        # GiteaForceRecreateRealDockerTests below for the real-Compose
        # proof that `db` is genuinely untouched, not just absent from the
        # command string).
        self.assertNotIn(" db", recreate_calls[0])
        # New values actually landed in $GITEA_ENV.
        with open(self.gitea_env) as f:
            content = f.read()
        self.assertIn("GITEA__server__DOMAIN=host.ts.net", content)
        self.assertIn("GITEA__server__ROOT_URL=https://host.ts.net/gitea",
                       content)

    # ── value changed, but container doesn't exist yet: skip, no error ──

    def test_changed_values_but_no_existing_container_skips_cleanly(self):
        result = self._run(
            publish_mode="tailscale", base_url="https://host.ts.net",
            container_exists=False,
            prev_domain="127.0.0.1:3000",
            prev_root_url="http://127.0.0.1:3000")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self._docker_log_lines()
        self.assertFalse(any("force-recreate" in line for line in lines),
                          lines)
        self.assertNotIn("WARNING", result.stderr)

    # ── only DOMAIN changes (ROOT_URL happens to match) still recreates ──

    def test_only_domain_changing_still_triggers_recreate(self):
        result = self._run(
            publish_mode="tailscale", base_url="https://host.ts.net",
            container_exists=True,
            prev_domain="old.ts.net",
            prev_root_url="https://host.ts.net/gitea")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self._docker_log_lines()
        self.assertEqual(
            len([line for line in lines if "force-recreate" in line]), 1,
            lines)

    # ── recreate failure warns, doesn't abort the install ────────────────

    def test_recreate_failure_warns_but_does_not_abort(self):
        result = self._run(
            publish_mode="tailscale", base_url="https://host.ts.net",
            container_exists=True, recreate_fails=True,
            prev_domain="127.0.0.1:3000",
            prev_root_url="http://127.0.0.1:3000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING", result.stderr)
        self.assertIn("server", result.stderr)


@unittest.skipUnless(HAVE_DOCKER, "docker not available")
class GiteaForceRecreateRealDockerTests(unittest.TestCase):
    """Real `docker compose` (not mocked), against a real, isolated
    Gitea+Postgres stack built from the actual `config/gitea-docker-
    compose.yml` -- proves `--no-deps` (docs/test-review.md Defect 2)
    actually stops `db` from being recreated with real Compose's own
    dependency-scope-inclusion + shared-`env_file`-config-hash-diffing
    behavior, which `GiteaForceRecreateTests`'s mocked `docker` cannot
    exercise (that gap is exactly how Defect 2 slipped through in the first
    place)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="aidswb-gitea-real-docker-")
        cls.gitea_dir = os.path.join(cls.tmp, "gitea")
        os.makedirs(os.path.join(cls.gitea_dir, "gitea"))
        os.makedirs(os.path.join(cls.gitea_dir, "postgres"))

        # Real compose file, container_name overridden to unique
        # per-process values -- config/gitea-docker-compose.yml hardcodes
        # `ai-dev-switchboard-gitea`/`ai-dev-switchboard-gitea-db`, which
        # would collide with any real, already-running install.
        with open(GITEA_COMPOSE_YML) as f:
            compose_yml = f.read()
        cls.server_name = f"aidswb-test-gitea-server-{os.getpid()}"
        cls.db_name = f"aidswb-test-gitea-db-{os.getpid()}"
        compose_yml = compose_yml.replace(
            "container_name: ai-dev-switchboard-gitea\n",
            f"container_name: {cls.server_name}\n")
        compose_yml = compose_yml.replace(
            "container_name: ai-dev-switchboard-gitea-db\n",
            f"container_name: {cls.db_name}\n")
        with open(os.path.join(cls.gitea_dir, "docker-compose.yml"),
                   "w") as f:
            f.write(compose_yml)

        cls.gitea_env = os.path.join(cls.gitea_dir, ".env")
        with open(cls.gitea_env, "w") as f:
            f.write(textwrap.dedent("""\
                POSTGRES_USER=gitea
                POSTGRES_DB=gitea
                GITEA__database__DB_TYPE=postgres
                GITEA__database__HOST=db:5432
                GITEA__database__NAME=gitea
                GITEA__database__USER=gitea
                GITEA__database__PASSWD=testpassword123
                GITEA__security__SECRET_KEY=abc123
                GITEA__security__INTERNAL_TOKEN=def456
                GITEA__security__INSTALL_LOCK=true
                GITEA__server__DOMAIN=127.0.0.1:3000
                GITEA__server__ROOT_URL=http://127.0.0.1:3000
                GITEA_PORT=13091
                GITEA_SSH_PORT=13092
                """))

        # COMPOSE_PROJECT_NAME set via env (not `-p` on the command line) so
        # it's picked up consistently by *every* `docker compose` call this
        # test makes, including the ones inside the real extracted "3b"
        # block below (which -- exactly like install.sh's own callers --
        # never passes `-p` itself, relying on this env var / the cwd's
        # directory-basename default instead). Without this, the setup call
        # here and the recreate block's own `docker compose ps -q server`/
        # `up -d --force-recreate --no-deps server` calls would resolve to
        # two different default Compose project names and never see the
        # same containers.
        cls.project = f"aidswb-test-gitea-{os.getpid()}"
        cls.docker_env = dict(os.environ)
        cls.docker_env["COMPOSE_PROJECT_NAME"] = cls.project
        r = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=cls.gitea_dir, capture_output=True, text=True, timeout=120,
            env=cls.docker_env)
        if r.returncode != 0:
            raise RuntimeError(f"setup 'docker compose up -d' failed: "
                                f"{r.stderr}")
        cls._wait_for_container(cls.server_name)
        cls._wait_for_container(cls.db_name)

    @classmethod
    def _wait_for_container(cls, name, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", name],
                capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip() == "true":
                return
            time.sleep(0.5)
        raise RuntimeError(f"{name} never reported Running")

    @classmethod
    def tearDownClass(cls):
        subprocess.run(
            ["docker", "compose", "down", "-v"],
            cwd=cls.gitea_dir, capture_output=True, text=True, timeout=60,
            env=cls.docker_env)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _created_at(name):
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.Created}}", name],
            capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def test_force_recreate_touches_server_only_not_db(self):
        server_created_before = self._created_at(self.server_name)
        db_created_before = self._created_at(self.db_name)

        # Change ROOT_URL/DOMAIN via the real set_env, same as install.sh.
        helpers = _extract_set_env_and_get_env()
        recreate_block = _extract_recreate_block()
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            {helpers}
            GITEA_DIR={self.gitea_dir!r}
            GITEA_ENV={self.gitea_env!r}
            GITEA_URL_PATH="/gitea"
            GITEA_PORT=13091
            PUBLISH_MODE="tailscale"
            BASE_URL="https://second.example.com"
            {recreate_block}
            """)
        result = subprocess.run(["bash", "-c", script],
                                 capture_output=True, text=True, timeout=60,
                                 env=self.docker_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "force-recreating", result.stdout.lower() + result.stderr.lower())

        # Give the recreated container a moment to actually come up before
        # inspecting it / reading its app.ini.
        self._wait_for_container(self.server_name)

        server_created_after = self._created_at(self.server_name)
        db_created_after = self._created_at(self.db_name)

        self.assertNotEqual(server_created_before, server_created_after,
                             "server should have been recreated")
        self.assertEqual(db_created_before, db_created_after,
                          "db must NOT be recreated (docs/test-review.md "
                          "Defect 2) -- its Created timestamp changed, "
                          "meaning --no-deps did not actually keep db out "
                          "of scope")

        # The whole point: the new values actually landed in app.ini, not
        # just in $GITEA_ENV.
        r = subprocess.run(
            ["docker", "exec", self.server_name, "cat",
             "/data/gitea/conf/app.ini"],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DOMAIN = second.example.com", r.stdout)
        self.assertIn("ROOT_URL = https://second.example.com/gitea",
                       r.stdout)


if __name__ == "__main__":
    unittest.main()
