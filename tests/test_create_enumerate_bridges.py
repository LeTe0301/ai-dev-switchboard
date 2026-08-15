#!/usr/bin/env python3
"""
Tests for ct/create.sh's `_enumerate_bridges()` (docs/BACKLOG.md item 32,
docs/spec.md "Fix 2 -- filter Proxmox's own per-container firewall bridges
out of the live bridge-enumeration menu"): `ip -o link show type bridge`
also lists the auto-created `fwbrNNNiM` bridges Proxmox creates for other
containers' firewall rules (e.g. `fwbr101i0`, `fwbr106i0`) alongside real
switch/uplink bridges (`vmbr0`). Picking one of these by mistake would
create a container with effectively no working uplink, and nothing in the
menu previously distinguished them from a real bridge.

Technique: extracts `_enumerate_bridges()` verbatim from ct/create.sh's
real source (same `_extract_between()`-style harness tests/test_install_
set_env.py established for install.sh's set_env()/get_env()), then runs it
against a stubbed `ip` shell function that emits canned `ip -o link show
type bridge`-style output -- so these tests exercise create.sh's actual
filter logic, not a reimplementation that could silently drift from the
real file.

Run with:
    python3 -m unittest discover -s tests -v
or just:
    python3 tests/test_create_enumerate_bridges.py
"""
import os
import subprocess
import textwrap
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
CREATE_SH = os.path.join(REPO_ROOT, "ct", "create.sh")


def _extract_between(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _extract_enumerate_bridges():
    """Pulls `_enumerate_bridges()` verbatim out of ct/create.sh's real
    source, unmodified -- so these tests exercise create.sh's actual
    filter logic, not a reimplementation that could silently drift from
    the real file."""
    with open(CREATE_SH) as f:
        source = f.read()
    return _extract_between(
        source, "_enumerate_bridges() {", 'msg "This creates a new LXC container'
    )


class EnumerateBridgesTests(unittest.TestCase):
    """Runs create.sh's real _enumerate_bridges() (extracted verbatim, see
    _extract_enumerate_bridges above) against a stubbed `ip` command,
    proving docs/spec.md's item-32 acceptance criterion: given a host with
    both `vmbr0` and one or more `fwbrNNNiM`-pattern interfaces present,
    the resulting BRIDGE_MENU_OPTS contains `vmbr0` but none of the
    `fwbrNNNiM` entries."""

    def setUp(self):
        self.enumerate_bridges = _extract_enumerate_bridges()

    def _run(self, ip_output, timeout=10):
        """Runs the extracted _enumerate_bridges() as a standalone bash
        script with `ip` stubbed as a shell function emitting `ip_output`
        for `ip -o link show type bridge` (shell functions take priority
        over PATH executables, so no real `ip` binary or /etc/pve is
        needed). Emits just the bridge *names* from BRIDGE_MENU_OPTS
        (every other element -- the array alternates name/description),
        one per line, for easy assertion. No /etc/pve/sdn/vnets.cfg in
        this sandbox, so the SDN-vnet branch of the function is a no-op
        here -- out of scope for this filter's own acceptance criterion."""
        script = textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            ip() {{
                if [ "$1" = "-o" ] && [ "$2" = "link" ] && [ "$3" = "show" ] && [ "$4" = "type" ] && [ "$5" = "bridge" ]; then
                    cat <<'IPEOF'
{ip_output}
IPEOF
                fi
            }}
            {self.enumerate_bridges}
            _enumerate_bridges
            for ((_i=0; _i<${{#BRIDGE_MENU_OPTS[@]}}; _i+=2)); do
                printf '%s\\n' "${{BRIDGE_MENU_OPTS[_i]}}"
            done
            """)
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=timeout)

    # ── the acceptance criterion, verbatim: real bridge + firewall
    #    bridges mixed together on one host ──────────────────────────────

    def test_real_bridges_kept_firewall_bridges_excluded(self):
        ip_output = (
            "2: vmbr0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue "
            "state UP mode DEFAULT group default qlen 1000\n"
            "3: vmbr1: <BROADCAST,MULTICAST> mtu 1500 qdisc noqueue state DOWN "
            "mode DEFAULT group default qlen 1000\n"
            "15: fwbr101i0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc "
            "noqueue state UP mode DEFAULT group default qlen 1000\n"
            "18: fwbr106i0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc "
            "noqueue state UP mode DEFAULT group default qlen 1000\n"
            "19: fwbr107i0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc "
            "noqueue state UP mode DEFAULT group default qlen 1000"
        )
        r = self._run(ip_output)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = r.stdout.splitlines()
        self.assertEqual(names, ["vmbr0", "vmbr1"])
        self.assertNotIn("fwbr101i0", names)
        self.assertNotIn("fwbr106i0", names)
        self.assertNotIn("fwbr107i0", names)

    # ── only firewall bridges present -> menu ends up empty ─────────────

    def test_only_firewall_bridges_yields_empty_menu(self):
        ip_output = (
            "15: fwbr101i0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc "
            "noqueue state UP mode DEFAULT group default qlen 1000\n"
            "18: fwbr106i0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc "
            "noqueue state UP mode DEFAULT group default qlen 1000"
        )
        r = self._run(ip_output)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "")

    # ── no firewall bridges present (regression: filter must not touch a
    #    host with no fwbrNNNiM interfaces at all) ───────────────────────

    def test_no_firewall_bridges_present_regression(self):
        ip_output = (
            "2: vmbr0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue "
            "state UP mode DEFAULT group default qlen 1000"
        )
        r = self._run(ip_output)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["vmbr0"])

    # ── the filter pattern must not be too broad: names that merely start
    #    with "fwbr" or contain "i<digits>" elsewhere must survive ───────

    def test_pattern_not_too_broad_similar_looking_names_survive(self):
        ip_output = (
            "2: vmbr0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue "
            "state UP mode DEFAULT group default qlen 1000\n"
            # starts with "fwbr" but no digits before "i" -- not the
            # Proxmox fwbrNNNiM naming convention.
            "5: fwbridge0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc "
            "noqueue state UP mode DEFAULT group default qlen 1000\n"
            # operator-named bridge that happens to contain "i0" but does
            # not start with "fwbr".
            "6: vmbr-media0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc "
            "noqueue state UP mode DEFAULT group default qlen 1000"
        )
        r = self._run(ip_output)
        self.assertEqual(r.returncode, 0, r.stderr)
        names = r.stdout.splitlines()
        self.assertEqual(names, ["vmbr0", "fwbridge0", "vmbr-media0"])

    # ── an interface name with an "@" suffix (e.g. a VLAN sub-bridge) is
    #    stripped by the existing `cut -d'@' -f1` before the filter runs;
    #    confirm the filter still excludes a firewall bridge in that case
    #    too, since `cut` runs upstream of the case pattern ─────────────

    def test_firewall_bridge_with_at_suffix_still_excluded(self):
        ip_output = (
            "2: vmbr0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue "
            "state UP mode DEFAULT group default qlen 1000\n"
            "15: fwbr101i0@if12: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 "
            "qdisc noqueue state UP mode DEFAULT group default qlen 1000"
        )
        r = self._run(ip_output)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["vmbr0"])


if __name__ == "__main__":
    unittest.main()
