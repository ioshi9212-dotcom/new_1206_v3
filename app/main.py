"""Railway/FastAPI entrypoint for Akira 1206 v3 standalone API."""
from app.production_runtime_patch import app

# Director Mode is a script-drafting layer on top of the existing runtime.
# It does not delete old live-game endpoints from the app; it only registers
# director endpoints and replaces /openapi-actions.json with a smaller
# Custom GPT Actions schema that exposes Director Mode only.
import app.director_runtime_patch  # noqa: F401,E402
import app.director_openapi_patch  # noqa: F401,E402

__all__ = ["app"]
