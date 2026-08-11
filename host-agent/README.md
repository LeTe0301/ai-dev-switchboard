# host-agent

Optional. Lets the switchboard's web UI show one extra row that controls a
persistent engine session directly on another machine — e.g. the Proxmox
host itself, outside any container, which is what this was built for
originally.

## Why a separate SSH channel instead of just another "project"

Per-project sessions (the switchboard's main feature) are spawned by the web
UI's own process, as RUN_USER, on the same machine the UI runs on. A host
session is deliberately different: it runs on a *different* machine (e.g.
the Proxmox host, which the switchboard's own container/VM should not have
general access to), so it's controlled over a narrowly-scoped SSH key
instead — one that can run exactly three commands
(`sudo ai-dev-switchboard-host-{start,stop,status}.sh`), nothing else.

## Setup

On the **controlled machine** (e.g. the Proxmox host):

1. Copy `host-start.sh`, `host-stop.sh`, `host-status.sh`, and `lib/` to
   `/usr/local/bin/` (script names must stay exactly
   `ai-dev-switchboard-host-{start,stop,status}.sh` — that's what the web
   UI's `host_run()` invokes over SSH).
2. Copy `host.env.example` to `/etc/ai-dev-switchboard/host.env` and edit it.
3. Copy (or symlink) your `engines.d/` directory to the path `host.env`
   points `ENGINES_DIR` at.
4. Create a dedicated user for the SSH channel (e.g. `switchboard`), and
   restrict its sudo rights to exactly those three scripts:
   ```
   switchboard ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-host-start.sh
   switchboard ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-host-stop.sh
   switchboard ALL=(root) NOPASSWD: /usr/local/bin/ai-dev-switchboard-host-status.sh
   ```
   (as a file under `/etc/sudoers.d/`, not by editing `/etc/sudoers`
   directly).
5. Generate an SSH keypair for that user and note the private key path.

On the **web UI machine**, set in `switchboard.env`:
```
HOST_CONTROL_ENABLED=1
HOST_CONTROL_KEY=/path/to/that/private/key
HOST_CONTROL_USER=switchboard
HOST_IP=<controlled machine's IP>
```

`install.sh --with-host-control` automates steps 1, 2 and 4 above when run
on the controlled machine; you still generate/place the SSH key yourself
(step 5) since that's the one part worth doing by hand rather than having
an install script mint credentials silently.
