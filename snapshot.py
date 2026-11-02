"""Record the live tmux layout + what runs in every pane (cron, every minute).

Writes snapshot.json only when something changed (previous one kept as snapshot.prev.json).
Does nothing while restore.py runs, during the first minutes after boot, or when tmux has no
sessions -- so a half-restored or empty server never overwrites a good snapshot.
"""
import logging
import os
import shutil
import sys
import time

import tmux_state as ts

log = logging.getLogger("snapshot")

FIELDS = ["session_name", "session_created", "window_index", "window_name", "automatic-rename",
          "window_layout", "window_active", "window_width", "window_height",
          "pane_index", "pane_active", "pane_pid", "pane_left", "pane_top"]
SEP = "|~|"   # tmux escapes control characters in -F output, so use a printable separator


def read(path, binary=False):
    try:
        with open(path, "rb" if binary else "r") as fh:
            return fh.read()
    except OSError:
        return None


def proc_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def foreground_pid(shell_pid):
    """pid of the pane's foreground process group leader, or None when the shell is idle."""
    stat = read(f"/proc/{shell_pid}/stat")
    if not stat:
        return None
    tpgid = int(stat.rsplit(")", 1)[1].split()[5])
    if tpgid <= 0 or tpgid == shell_pid or not os.path.exists(f"/proc/{tpgid}"):
        return None
    return tpgid


def describe_pane(shell_pid):
    """-> (cwd, venv, argv or None, note)"""
    cwd = proc_cwd(shell_pid)
    fg = foreground_pid(shell_pid)
    if fg is None:
        return cwd, None, None, ""
    raw = read(f"/proc/{fg}/cmdline", binary=True) or b""
    argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
    if not argv:
        return cwd, None, None, ""
    fg_cwd = proc_cwd(fg) or cwd
    env = read(f"/proc/{fg}/environ", binary=True) or b""
    venv = None
    for item in env.split(b"\0"):
        if item.startswith(b"VIRTUAL_ENV="):
            venv = item.split(b"=", 1)[1].decode("utf-8", "replace") or None
    prog = os.path.basename(argv[0])
    if any(fg_cwd.startswith(d) for d in ts.NO_AUTOSTART_DIRS) or \
            any(d in a for a in argv for d in ts.NO_AUTOSTART_DIRS):
        return fg_cwd, venv, None, "manual start only: " + " ".join(argv)
    if not prog.startswith(ts.RESTART_PREFIXES):
        return cwd, None, None, "not restarted: " + " ".join(argv)
    return fg_cwd, venv, argv, ""


def take():
    res = ts.tmux("list-panes", "-a", "-F", SEP.join("#{%s}" % f for f in FIELDS), check=False)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    sessions = {}
    for line in res.stdout.splitlines():
        r = dict(zip(FIELDS, line.split(SEP)))
        if r["session_name"] in ts.SKIP_SESSIONS:
            continue
        s = sessions.setdefault(r["session_name"], {
            "name": r["session_name"], "created": int(r["session_created"]), "windows": {}})
        w = s["windows"].setdefault(int(r["window_index"]), {
            "index": int(r["window_index"]), "name": r["window_name"],
            "auto_rename": r["automatic-rename"] == "1", "layout": r["window_layout"],
            "w": int(r["window_width"]), "h": int(r["window_height"]),
            "active": r["window_active"] == "1", "panes": []})
        cwd, venv, argv, note = describe_pane(int(r["pane_pid"]))
        w["panes"].append({"index": int(r["pane_index"]), "active": r["pane_active"] == "1",
                           "left": int(r["pane_left"]), "top": int(r["pane_top"]),
                           "cwd": cwd or "/root", "venv": venv, "cmd": argv, "note": note})
    out = []
    for s in sorted(sessions.values(), key=lambda x: (x["created"], x["name"])):
        s["windows"] = [s["windows"][i] for i in sorted(s["windows"])]
        for w in s["windows"]:
            w["panes"].sort(key=lambda p: p["index"])
        out.append(s)
    return out


def summary(sessions):
    parts = []
    for s in sessions:
        for w in s["windows"]:
            for p in w["panes"]:
                what = " ".join(p["cmd"]) if p["cmd"] else (p["note"] or "shell")
                parts.append(f"{s['name']}:{w['index']}.{p['index']} {what}")
    return parts


def main():
    ts.setup_logging()
    if ts.lock_active():
        log.debug("restore in progress, skip")
        return 0
    if ts.uptime() < ts.BOOT_GRACE and not os.environ.get("FORCE"):
        log.debug("boot grace period, skip")
        return 0
    sessions = take()
    if not sessions:
        log.debug("no tmux sessions, keep the old snapshot")
        return 0
    old = None
    if os.path.exists(ts.SNAPSHOT):
        try:
            old = ts.load()
        except (OSError, ValueError):
            log.warning("old snapshot unreadable, overwriting")
    if old and old.get("sessions") == sessions:
        return 0
    if old:
        shutil.copyfile(ts.SNAPSHOT, ts.SNAPSHOT_PREV)
    ts.save_atomic({"taken": time.time(), "sessions": sessions})
    before = set(summary(old["sessions"])) if old else set()
    after = set(summary(sessions))
    for line in sorted(after - before):
        log.info("+ %s", line)
    for line in sorted(before - after):
        log.info("- %s", line)
    log.info("snapshot saved: %d sessions, %d panes", len(sessions), len(after))
    return 0


if __name__ == "__main__":
    sys.exit(main())
