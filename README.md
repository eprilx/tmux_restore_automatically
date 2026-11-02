# tmux_restore_automatically

Bring back your **entire tmux setup** after a server reboot: the same sessions, the same windows
(index and name), the same pane splits (`Ctrl-b %` / `Ctrl-b "`), the same working directory in every
pane, and **the Python programs that were running in those panes**, each restarted inside its own
virtualenv.

You don't describe the layout by hand. Once a minute a snapshot is taken from the live tmux server.
On reboot, the latest snapshot is rebuilt. Split panes, start new sessions, change commands: just
work as usual and the snapshot follows.

Standard library only. Nothing to `pip install`.

## Requirements

| | |
|---|---|
| OS | Linux (reads `/proc`). Does not run on macOS or Windows. |
| tmux | ≥ 2.9 (`resize-window` and the `window-size` option) |
| Python | ≥ 3.8 at `/usr/bin/python3` (`shlex.join`) |
| cron | with `@reboot` support (present by default on Ubuntu/Debian) |

Tested on Ubuntu 24.04, tmux 3.4, Python 3.12.

## Layout

```
tmux_restore_automatically/
├── tmux_state.py               # defaults + shared helpers (run tmux, load/save the snapshot)
├── snapshot.py                 # cron, every minute: record the tmux state -> snapshot.json
├── restore.py                  # cron @reboot: rebuild tmux from snapshot.json
└── local_settings.example.py   # template for per-server overrides (copy to local_settings.py)
```

Files created at runtime (already in `.gitignore`):

| File | Meaning |
|---|---|
| `snapshot.json` | latest tmux state |
| `snapshot.prev.json` | the snapshot before the most recent change |
| `snapshot.boot.json` | the snapshot used by the last reboot |
| `snapshot.log` | one line per pane that changed (`+` / `-`); silent when nothing changes |
| `restore.log` | log of every restore |
| `restoring.lock` | exists while `restore.py` is running |

## Installation

### 1. Copy the code to the server

From your machine (on Windows use git-bash):

```bash
cd tmux_restore_automatically
tar cf - tmux_state.py snapshot.py restore.py local_settings.example.py | ssh root@<IP> 'mkdir -p /root/tmux_restore && cd /root/tmux_restore && tar xf - && chown root:root *.py'
```

Or clone the repository straight into that directory:

```bash
git clone https://github.com/<user>/tmux_restore_automatically.git /root/tmux_restore
```

The rest of this guide assumes the scripts live in `/root/tmux_restore`.

> The files must have Linux line endings (LF). The repository's `.gitattributes` enforces LF. If you
> copy files by hand from Windows, run `sed -i 's/\r$//' *.py` on the server.

### 2. Adjust the settings (optional)

Defaults live at the top of `tmux_state.py`. Don't edit them there: put per-server values in
`local_settings.py` next to the scripts (git-ignored, so `git pull` never conflicts with it):

```bash
cp local_settings.example.py local_settings.py   # then edit
```

Any setting below can be overridden there:

| Setting | Default | Meaning |
|---|---|---|
| `SKIP_SESSIONS` | `set()` | Sessions that are **never** rebuilt, e.g. sessions owned by another start script that refuses to run when the session already exists. |
| `NO_AUTOSTART_DIRS` | `()` | Processes running in these directories are **not** restarted. Their pane comes back as an idle shell. Use it for anything you want to start by hand, such as bots that trade real money. |
| `RESTART_PREFIXES` | `("python",)` | Only programs whose name starts with one of these prefixes are restarted. Anything else (`htop`, `tail`, `sqlite3`, ...) comes back as an idle shell. |
| `BOOT_GRACE` | `600` | Seconds after boot during which `snapshot.py` writes nothing, so a half-rebuilt server never overwrites a good snapshot. |
| `LOCK_MAX_AGE` | `900` | A lock older than this many seconds is treated as stale. |
| `DNS_HOST` / `DNS_WAIT` | `dns.google` / `180` | On reboot, wait until this host resolves (up to N seconds) before starting commands. Set it to a host your programs need. |

### 3. Take the first snapshot

```bash
cd /root/tmux_restore
FORCE=1 python3 snapshot.py      # FORCE skips BOOT_GRACE if the server booted recently
cat snapshot.json                # check which pane will run which command
```

### 4. Dry run (your real tmux is not touched)

```bash
DRY=1 SOCK=restore_test python3 restore.py
tmux -L restore_test attach      # look at the rebuilt layout; Ctrl-b d to detach
tmux -L restore_test kill-server # clean up
```

- `SOCK=restore_test` builds everything on a separate tmux server (`tmux -L restore_test`), so your
  real sessions are not affected.
- `DRY=1` types each command with a leading `#`, so **nothing runs**. It also skips the DNS wait and
  the lock file.

### 5. Install the cron jobs

```bash
(crontab -l 2>/dev/null | grep -v tmux_restore
 echo "* * * * * /usr/bin/python3 /root/tmux_restore/snapshot.py >> /root/tmux_restore/snapshot.log 2>&1"
 echo "@reboot /usr/bin/python3 /root/tmux_restore/restore.py >> /root/tmux_restore/restore.log 2>&1"
) | crontab -
crontab -l
```

Make sure cron is enabled: `systemctl is-enabled cron && systemctl is-active cron`.

## How it works

### snapshot.py (every minute)

1. `tmux list-panes -a` gives sessions, windows (index, name, `automatic-rename`), layout string,
   size and the active pane.
2. For each pane it reads `/proc/<pane_pid>/stat` to get `tpgid`, the foreground process group:
   - `tpgid == pane_pid`: the shell is idle, recorded as an idle shell.
   - otherwise it reads `/proc/<tpgid>/cmdline`, `cwd` and `environ` to get the full command line,
     its directory and `VIRTUAL_ENV`. That is how the right venv is known even when you typed a bare
     `python x.py` after `source venv/bin/activate`.
3. The file is written only when something changed (atomic tmp + rename; the old one is kept as
   `snapshot.prev.json`).
4. It **writes nothing** during `BOOT_GRACE`, while `restore.py` runs (lock file), or when tmux has
   no sessions. A good snapshot is never replaced by an empty or half-rebuilt state.

### restore.py (@reboot)

1. Creates the lock, saves `snapshot.boot.json`, waits for DNS.
2. For every session, in creation order (an existing session is skipped, so running it twice is
   harmless):
   - creates each window at its original index. Windows with a manually set name keep it, windows
     with automatic renaming are left to tmux;
   - creates the panes by always splitting the **last** pane, so the pane order matches the saved
     layout, then applies `select-layout <saved layout>` for an identical split. Afterwards the window
     goes back to following the attached client's size;
   - types each command into its pane as if you typed it: `source <venv>/bin/activate && <command>`.
     After `Ctrl-C` you are left in a shell with the venv active, and the Up arrow restarts it.
3. Re-selects the active pane and window, removes the lock.

## Day-to-day use

- Current snapshot: `python3 -m json.tool /root/tmux_restore/snapshot.json | less`
- Change history: `tail -f /root/tmux_restore/snapshot.log`
- After a reboot: `cat /root/tmux_restore/restore.log`
- Manual restore (e.g. after an accidental `tmux kill-server`): `python3 /root/tmux_restore/restore.py`.
  It only creates the sessions that are missing.

## Caveats

- The snapshot reflects the state when it was taken. If a process crashed and its pane sat at a shell
  prompt for more than a minute before the reboot, that pane comes back as an idle shell.
- Panes opened or changed in the last minute before a reboot may not be in the snapshot yet.
- An idle shell's venv cannot be recorded (a shell's own `VIRTUAL_ENV` is not visible in `/proc`), so
  idle panes come back without an activated venv.
- If a command's venv no longer exists, the command is still typed but **without**
  `source activate`, so it will run with the system Python.
- Don't copy `snapshot.json` between servers: directories, venvs and `pane-base-index` may differ.
  Install the code on the new server and let it take its own snapshot.
- Processes outside tmux (Docker, systemd, ...) are out of scope. For Docker use
  `--restart unless-stopped`.
- Programs that trade real money: add their directory to `NO_AUTOSTART_DIRS` or their session to
  `SKIP_SESSIONS`. Before letting an unknown script restart automatically, check whether it places
  orders, for example:
  `grep -nE "create_order|place_order|createOrder|create_market" script.py`.

## Uninstall

```bash
crontab -l | grep -v tmux_restore | crontab -
rm -rf /root/tmux_restore
```
