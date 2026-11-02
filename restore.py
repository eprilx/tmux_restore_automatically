"""Rebuild every tmux session from snapshot.json (crontab @reboot).

Sessions, windows (index, name), panes (exact split layout, cwd) are recreated; each pane's
python command is typed back in with its venv activated, so Ctrl-C leaves a shell with the venv on.
Anything listed in tmux_state.NO_AUTOSTART_DIRS / SKIP_SESSIONS is never started.
A session that already exists is left untouched, so running this twice is harmless.

Dry test on a separate tmux server:  DRY=1 SOCK=restore_test python3 restore.py
(commands are typed with a leading '#', nothing runs; kill with: tmux -L restore_test kill-server)
"""
import logging
import os
import shlex
import shutil
import socket
import sys
import time

import tmux_state as ts

log = logging.getLogger("restore")
DRY = bool(os.environ.get("DRY"))


def wait_network():
    end = time.time() + ts.DNS_WAIT
    while True:
        try:
            socket.getaddrinfo(ts.DNS_HOST, 443)
            log.info("network ok")
            return
        except OSError:
            if time.time() > end:
                log.warning("DNS still failing after %d s, starting anyway", ts.DNS_WAIT)
                return
            time.sleep(5)


def has_session(name):
    return ts.tmux("has-session", "-t", "=" + name, check=False).returncode == 0


def pane_dir(p):
    return p["cwd"] if os.path.isdir(p["cwd"]) else "/root"


def build_window(sname, w, first):
    target = f"{sname}:{w['index']}"
    p0 = w["panes"][0]
    name_opt = [] if w["auto_rename"] else ["-n", w["name"]]
    if first:
        ts.tmux("new-session", "-d", "-s", sname, "-x", str(w["w"]), "-y", str(w["h"]),
                "-c", pane_dir(p0), *name_opt)
        ts.tmux("set", "-g", "default-shell", "/bin/bash")
        base = ts.tmux("list-windows", "-t", "=" + sname, "-F", "#{window_index}").stdout.split()[0]
        if int(base) != w["index"]:
            ts.tmux("move-window", "-s", f"{sname}:{base}", "-t", target)
    else:
        ts.tmux("new-window", "-d", "-t", target, "-c", pane_dir(p0), *name_opt)
    for p in w["panes"][1:]:
        # split the LAST pane so the new one is appended: pane order then matches the saved
        # pane order, which select-layout maps onto the layout cells one by one
        last = ts.tmux("list-panes", "-t", target, "-F", "#{pane_index}").stdout.split()[-1]
        ts.tmux("split-window", "-d", "-t", f"{target}.{last}", "-c", pane_dir(p))
        ts.tmux("select-layout", "-t", target, "tiled")      # keep room for the next split
    ts.tmux("resize-window", "-t", target, "-x", str(w["w"]), "-y", str(w["h"]))
    if len(w["panes"]) > 1:
        ts.tmux("select-layout", "-t", target, w["layout"])
    ts.tmux("set-window", "-u", "-t", target, "window-size")   # back to following the client


def start_commands(sname, w):
    for p in w["panes"]:
        target = f"{sname}:{w['index']}.{p['index']}"
        if p["cmd"]:
            line = shlex.join(p["cmd"])
            if p["venv"] and os.path.isfile(os.path.join(p["venv"], "bin", "activate")):
                line = f"source {shlex.quote(os.path.join(p['venv'], 'bin', 'activate'))} && {line}"
            if DRY:
                line = "# " + line
            ts.tmux("send-keys", "-t", target, line, "Enter")
            log.info("  %s: %s", target, line)
        elif p["note"]:
            log.info("  %s: idle (%s)", target, p["note"])


def restore_session(s):
    sname = s["name"]
    for i, w in enumerate(s["windows"]):
        build_window(sname, w, first=(i == 0))
    for w in s["windows"]:
        start_commands(sname, w)
        active = [p for p in w["panes"] if p["active"]]
        if active:
            ts.tmux("select-pane", "-t", f"{sname}:{w['index']}.{active[0]['index']}")
    active_w = [w for w in s["windows"] if w["active"]]
    if active_w:
        ts.tmux("select-window", "-t", f"{sname}:{active_w[0]['index']}")


def main():
    ts.setup_logging()
    os.environ["SHELL"] = "/bin/bash"          # cron gives /bin/sh; tmux takes default-shell from it
    if not os.path.exists(ts.SNAPSHOT):
        log.error("no snapshot at %s", ts.SNAPSHOT)
        return 1
    snap = ts.load()
    log.info("restoring snapshot taken %s", time.strftime("%F %T", time.localtime(snap["taken"])))
    if not DRY:
        with open(ts.LOCK, "w") as fh:
            fh.write(str(os.getpid()))
        shutil.copyfile(ts.SNAPSHOT, ts.SNAPSHOT_BOOT)
    try:
        if not DRY:
            wait_network()
        for s in snap["sessions"]:
            if s["name"] in ts.SKIP_SESSIONS:
                continue
            if has_session(s["name"]):
                log.info("session %s exists, skip", s["name"])
                continue
            try:
                restore_session(s)
                log.info("session %s restored (%d windows)", s["name"], len(s["windows"]))
            except RuntimeError as exc:
                log.error("session %s: %s", s["name"], exc)
    finally:
        if not DRY:
            try:
                os.remove(ts.LOCK)
            except OSError:
                pass
    log.info("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
