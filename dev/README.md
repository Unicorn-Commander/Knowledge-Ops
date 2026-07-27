Run `python3 dev/mock_api.py` (default port 8765; pass a port or set `MOCK_ADMIN=1` to boot as admin), then open http://127.0.0.1:8765/ui/ (console) or /landing/ (public page).
Stdlib-only offline harness: serves the static UI plus canned JSON for every /kb.* endpoint the UI calls.
