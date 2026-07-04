"""Railway/FastAPI entrypoint for Akira 1206 v3 standalone API."""
from importlib import import_module

from app.production_runtime_patch import app

# Director Mode is a script-drafting layer on top of the existing runtime.
# It does not delete old live-game endpoints from the app; it only registers
# director endpoints and replaces /openapi-actions.json with a smaller
# Custom GPT Actions schema that exposes Director Mode only.
#
# Important: do NOT use `import app.director_runtime_patch` here.
# In this module it would rebind the exported name `app` to the Python package
# instead of the FastAPI instance, and Uvicorn would crash with:
# TypeError: 'module' object is not callable.
import_module("app.director_runtime_patch")
import_module("app.director_openapi_patch")

__all__ = ["app"]
