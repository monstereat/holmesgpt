"""FastAPI entry point for the local incident service."""

from fastapi import FastAPI

app = FastAPI(title="Holmes AIOps Incident Service", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
