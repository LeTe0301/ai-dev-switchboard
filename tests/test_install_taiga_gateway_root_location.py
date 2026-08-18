#!/usr/bin/env python3
"""
Regression tests for `docs/test-review.md` Defect 1 (post-approval
loop-back, backlog item 47(a)): `install.sh`'s generated
`docker-compose.override.taiga-gateway.conf` `location /` block looked like
the same variable-`proxy_pass` fix items 42/43 already applied successfully
to `/api/`/`/admin/`/`/media/` -- but the reviewer live-tested it (real
nginx + real backend, then a real headless Playwright pass through a
subpath-stripping proxy) and found it collapses *every* request path to a
bare `/`, so taiga-front's `index.html` is served for every asset/API/
conf.json request instead of the real file. Root cause: unlike the other
locations (whose `proxy_pass` target has a real trailing path segment after
the variable, e.g. `.../api/`), `location /`'s target ended in a bare "/"
with nothing else -- for that specific bare-URI + variable-upstream
combination, nginx does not append the client's actual request path to the
forwarded request. Fixed by appending `$request_uri` (nginx's own raw,
unmodified request URI, including any query string) to the `proxy_pass`
target.

Two layers of coverage, matching this repo's own `HAVE_GIT`-style
tool-availability-gated pattern (`tests/test_gitea_sync_project.py`):

1. A cheap, always-run static check (`ProxyPassLineTests`) on the real
   heredoc content extracted verbatim out of `install.sh` -- catches an
   accidental revert of the fix (or the fix being applied to the wrong
   location) without needing Docker.
2. A real-nginx, real-backend runtime test (`RootLocationRuntimeTests`,
   `@unittest.skipUnless(HAVE_DOCKER, ...)`) that actually reproduces the
   defect the way the reviewer did: runs the exact extracted heredoc as a
   real nginx server, fronting a real fake-backend container standing in
   for `taiga-front`, and confirms distinct real content comes back for
   distinct paths (not the same bytes for everything) -- a static string
   check alone cannot catch this class of bug, which is exactly why the
   original (incorrect) "this is already fixed, the syntax matches" claim
   in `docs/implementation.md` slipped through in the first place.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_install_taiga_gateway_root_location.py
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.request

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
INSTALL_SH = os.path.join(REPO_ROOT, "install.sh")

HAVE_DOCKER = shutil.which("docker") is not None


def _extract_between(source, start_marker, end_marker):
    start = source.index(start_marker) + len(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _extract_gateway_conf():
    """The real nginx conf install.sh writes to
    $TAIGA_DIR/docker-compose.override.taiga-gateway.conf, extracted
    verbatim from between the heredoc's opening and closing 'NGINX'
    delimiters -- not a re-typed copy that could drift from the real
    file."""
    with open(INSTALL_SH) as f:
        source = f.read()
    return _extract_between(
        source,
        "docker-compose.override.taiga-gateway.conf\" <<'NGINX'\n",
        "\nNGINX",
    )


class ProxyPassLineTests(unittest.TestCase):
    """Static, always-run guard against reverting/misapplying the fix --
    complements (does not replace) the real-nginx runtime test below."""

    def setUp(self):
        self.conf = _extract_gateway_conf()

    def test_root_location_proxy_pass_forwards_the_real_request_uri(self):
        self.assertIn(
            "proxy_pass http://$upstream_front$request_uri;", self.conf,
            "location / must forward $request_uri, not a bare '/' -- a "
            "bare '/' is exactly the regression docs/test-review.md "
            "Defect 1 found live.")
        self.assertNotIn("proxy_pass http://$upstream_front/;", self.conf)

    def test_other_locations_left_untouched(self):
        # docs/test-review.md confirmed /api/, /admin/, /media/ already
        # forward correctly today -- this is a narrow, targeted fix for
        # location / only, not a blanket rewrite.
        self.assertIn("proxy_pass http://$upstream_back:8000/api/;",
                       self.conf)
        self.assertIn("proxy_pass http://$upstream_back:8000/admin/;",
                       self.conf)
        self.assertIn("proxy_pass http://$upstream_protected:8003/;",
                       self.conf)


@unittest.skipUnless(HAVE_DOCKER, "docker not available")
class RootLocationRuntimeTests(unittest.TestCase):
    """Real nginx (the same pinned taiga-gateway image, nginx:1.19-alpine)
    fronting a real fake-backend container standing in for taiga-front, on
    an isolated scratch Docker network -- no contact with any live
    ai-dev-switchboard-taiga* containers. Reproduces docs/test-review.md's
    own repro technique (distinct real content per path, not identical
    bytes for everything) against the real extracted conf, not a
    hand-retyped copy."""

    NETWORK = None
    FRONT = None
    GATEWAY = None
    PORT = 19191

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="aidswb-taiga-root-location-")
        cls.front_root = os.path.join(cls.tmp, "fake-front")
        os.makedirs(os.path.join(cls.front_root, "js"))
        with open(os.path.join(cls.front_root, "index.html"), "w") as f:
            f.write("INDEX-CONTENT")
        with open(os.path.join(cls.front_root, "conf.json"), "w") as f:
            f.write('{"marker":"CONF-JSON-CONTENT"}')
        with open(os.path.join(cls.front_root, "js", "app-loader.js"),
                   "w") as f:
            f.write("APP-LOADER-JS-CONTENT")

        cls.conf_path = os.path.join(cls.tmp, "gateway.conf")
        with open(cls.conf_path, "w") as f:
            f.write(_extract_gateway_conf())

        cls.network = f"aidswb-test-taiga-root-location-{os.getpid()}"
        cls.front = f"aidswb-test-taiga-front-{os.getpid()}"
        cls.gateway = f"aidswb-test-taiga-gateway-{os.getpid()}"

        subprocess.run(["docker", "network", "create", cls.network],
                        capture_output=True, text=True, check=True)
        subprocess.run(
            ["docker", "run", "-d", "--name", cls.front,
             "--network", cls.network, "--network-alias", "taiga-front",
             "-v", f"{cls.front_root}:/usr/share/nginx/html:ro",
             "nginx:1.19-alpine"],
            capture_output=True, text=True, check=True)
        subprocess.run(
            ["docker", "run", "-d", "--name", cls.gateway,
             "--network", cls.network, "-p", f"{cls.PORT}:80",
             "-v", f"{cls.conf_path}:/etc/nginx/conf.d/default.conf:ro",
             "nginx:1.19-alpine"],
            capture_output=True, text=True, check=True)
        # Give both containers a moment to actually start serving.
        cls._wait_for_gateway()

    @classmethod
    def _wait_for_gateway(cls, timeout=15):
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.PORT}/", timeout=2)
                return
            except Exception as e:  # noqa: BLE001 -- just a readiness poll
                last_err = e
                time.sleep(0.5)
        raise RuntimeError(f"gateway never became ready: {last_err}")

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["docker", "rm", "-f", cls.gateway, cls.front],
                        capture_output=True, text=True)
        subprocess.run(["docker", "network", "rm", cls.network],
                        capture_output=True, text=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _get(self, path):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.PORT}{path}", timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_root_returns_index(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertEqual(body, "INDEX-CONTENT")

    def test_conf_json_returns_real_conf_json_not_index(self):
        status, body = self._get("/conf.json")
        self.assertEqual(status, 200)
        self.assertEqual(body, '{"marker":"CONF-JSON-CONTENT"}')

    def test_nested_asset_path_returns_real_asset_not_index(self):
        status, body = self._get("/js/app-loader.js")
        self.assertEqual(status, 200)
        self.assertEqual(body, "APP-LOADER-JS-CONTENT")

    def test_query_string_is_preserved(self):
        status, body = self._get("/conf.json?cachebust=123")
        self.assertEqual(status, 200)
        self.assertEqual(body, '{"marker":"CONF-JSON-CONTENT"}')

    def test_nonexistent_path_404s_distinctly_not_index_fallback(self):
        status, body = self._get("/this-path-does-not-exist-xyz123")
        self.assertEqual(status, 404)
        self.assertNotEqual(body, "INDEX-CONTENT")

    def test_three_distinct_paths_return_three_distinct_bodies(self):
        # The exact shape of the regression: before the fix, all of these
        # returned byte-identical index.html content.
        bodies = {self._get(p)[1] for p in
                  ("/", "/conf.json", "/js/app-loader.js")}
        self.assertEqual(len(bodies), 3, bodies)


if __name__ == "__main__":
    unittest.main()
