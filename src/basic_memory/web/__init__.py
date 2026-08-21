"""`bm web` — the read-only board server (GAPS U41).

Nothing in this package is on the fast CLI path. It is the one corner of the
tree allowed to import FastAPI, uvicorn and Jinja2, because it *is* a
long-lived server: the import cost the native-verb guard exists to keep off
`bm ls` is paid once at boot and never again. `cli/commands/web.py` is the
boundary that keeps it that way — it defers every import in this package into
the function that starts the server.
"""
