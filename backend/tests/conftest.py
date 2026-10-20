"""Import backend/main.py without touching the network.

main.py starts its background maintenance thread at module load and reads its
config from env, so: point Supabase at a closed local port with retries off
(any accidental call fails fast instead of hanging), and stub Thread.start for
the duration of the import so the bg loop never runs under pytest."""
import os, sys, threading, importlib

os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SUPABASE_KEY", "test-anon")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service")
os.environ.setdefault("HTTP_RETRY_TOTAL", "0")
os.environ.setdefault("SLACK_WEBHOOK_URL", "http://127.0.0.1:9/slack")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("TRANSCRIBE_ENABLED", "1")
os.environ.setdefault("ANALYZE_ENABLED", "1")
os.environ.pop("SLACK_SIGNING_SECRET", None)
os.environ.pop("SLACK_APPROVER_USER_IDS", None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_real_start = threading.Thread.start
threading.Thread.start = lambda self: None
try:
    main = importlib.import_module("main")
finally:
    threading.Thread.start = _real_start

import pytest

@pytest.fixture
def m():
    return main

@pytest.fixture
def no_network(monkeypatch):
    """Belt and braces: make every req_lib verb raise so a test that reaches
    the network fails loudly instead of waiting on a refused connection."""
    class Boom:
        def __getattr__(self, name):
            def _raise(*a, **k):
                raise AssertionError(f"unexpected network call req_lib.{name} {a[:1]}")
            return _raise
    monkeypatch.setattr(main, "req_lib", Boom())
    monkeypatch.setattr(main, "audit_log", lambda *a, **k: None)
