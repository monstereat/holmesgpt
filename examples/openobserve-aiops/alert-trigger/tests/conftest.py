import os


os.environ.setdefault("AIOPS_ENV", "local")
os.environ.setdefault("AIOPS_AUTH_MODE", "local")
os.environ.setdefault("SESSION_SIGNING_KEY", "test-only-session-signing-key-not-for-runtime")
