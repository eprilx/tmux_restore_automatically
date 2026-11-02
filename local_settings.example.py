# Per-server overrides for tmux_state.py.
# Copy to local_settings.py on the server and edit; local_settings.py is git-ignored.

# sessions that are never rebuilt (e.g. a session another start script creates itself)
SKIP_SESSIONS = {"my_manual_session"}

# processes running under these directories are never restarted (their pane comes back idle)
NO_AUTOSTART_DIRS = ("/root/my_project/manual_only",)

# wait until this name resolves on boot before starting anything
DNS_HOST = "api.example.com"
