"""Shared bits for snapshot.py / restore.py (stdlib only, Python 3.8+, Linux)."""
import json
import logging
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT = os.environ.get("SNAPSHOT") or os.path.join(HERE, "snapshot.json")
SNAPSHOT_PREV = os.path.join(HERE, "snapshot.prev.json")
SNAPSHOT_BOOT = os.path.join(HERE, "snapshot.boot.json")
LOCK = os.path.join(HERE, "restoring.lock")

# ---- settings (override any of them in local_settings.py next to this file; it is git-ignored)
# sessions that are never rebuilt, e.g. ones owned by another start script
SKIP_SESSIONS = set()
# processes running under these directories are never restarted; their pane comes back idle
# (use it for anything you want to start by hand)
NO_AUTOSTART_DIRS = ()
# only these foreground programs are re-run (by basename prefix); anything else -> idle shell
RESTART_PREFIXES = ("python",)

LOCK_MAX_AGE = 900          # s, an older lock is considered stale
BOOT_GRACE = 600            # s after boot during which snapshot.py does nothing
DNS_HOST = "dns.google"     # on reboot, wait until this name resolves before starting commands
DNS_WAIT = 180              # s

try:
    from local_settings import *  # noqa: F401,F403  (per-server overrides)
except ImportError:
    pass


def setup_logging():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")


def tmux(*args, check=True):
    """Run tmux on the default socket (or `tmux -L $SOCK` for tests)."""
    cmd = ["tmux"]
    if os.environ.get("SOCK"):
        cmd += ["-L", os.environ["SOCK"]]
    res = subprocess.run(cmd + list(args), capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)}: {res.stderr.strip()}")
    return res


def load(path=SNAPSHOT):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_atomic(data, path=SNAPSHOT):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def lock_active():
    try:
        return time.time() - os.path.getmtime(LOCK) < LOCK_MAX_AGE
    except OSError:
        return False


def uptime():
    with open("/proc/uptime") as fh:
        return float(fh.read().split()[0])
