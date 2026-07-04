# Director Mode Actions import fix v2

Fixes Custom GPT Actions import errors:

- `('openapi',): Input should be '3.1.1' or '3.1.0'`
- `object schema missing properties` for response schemas

Changes:
- `openapi` is now `3.1.0`
- all object schemas include `properties`
- generic response schema includes `properties` and `additionalProperties: true`
- includes safe `app/main.py` fix so Uvicorn exports the FastAPI instance, not the Python module

Install:
1. unzip over the repository root with replacement
2. deploy Railway
3. open `/health`
4. open `/openapi-actions.json`
5. import the schema URL in Custom GPT Actions
