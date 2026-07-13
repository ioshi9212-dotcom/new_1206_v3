"""Railway/FastAPI entrypoint for Akira 1206 v3 standalone API."""
from importlib import import_module

from app.production_runtime_patch import app

# The live-game scene gate must be registered after the transactional writer and
# context builder, but before Director Mode adds its separate endpoints.
import_module("app.scene_validation_runtime_patch")

# Director Mode is a script-drafting layer on top of the existing runtime.
# Live gameplay remains at /openapi-actions.json; Director Mode has its own
# schema at /openapi-director-actions.json.
#
# Important: do NOT use `import app.director_runtime_patch` here.
# In this module it would rebind the exported name `app` to the Python package
# instead of the FastAPI instance, and Uvicorn would crash with:
# TypeError: 'module' object is not callable.
import_module("app.director_runtime_patch")
import_module("app.director_openapi_patch")

__all__ = ["app"]
