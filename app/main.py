"""Railway/FastAPI entrypoint for Akira 1206 v3 standalone API."""
from app.production_runtime_patch import app

__all__ = ["app"]
