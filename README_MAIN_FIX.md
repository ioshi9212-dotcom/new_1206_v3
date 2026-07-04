# Director Mode main.py fix

Fixes Railway crash:

TypeError: 'module' object is not callable

Cause:
`import app.director_runtime_patch` inside app/main.py rebounded the exported `app`
name from the FastAPI instance to the Python package module.

Fix:
Use importlib.import_module(...) so patches load without overwriting `app`.
