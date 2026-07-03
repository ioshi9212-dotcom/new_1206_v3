"""Custom GPT Actions schema for Director Mode only.

Old live-game routes remain registered in FastAPI for internal/manual use, but
/openapi-actions.json exposes only the safer script-drafting endpoints.
"""
from __future__ import annotations

from typing import Any

from app import compact as base
from app.compact import app
from app.director_runtime_patch import DIRECTOR_RUNTIME_VERSION


def _object_schema(properties: dict | None = None, *, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": True,
    }
    if required:
        schema["required"] = required
    return schema


def _response(description: str, schema: dict | None = None) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": schema or {"type": "object", "properties": {}, "additionalProperties": True}
            }
        },
    }


def _draft_path_param() -> dict[str, Any]:
    return {"name": "draft_id", "in": "path", "required": True, "schema": {"type": "string"}}


object_any = {"type": "object", "properties": {}, "additionalProperties": True}
array_string = {"type": "array", "items": {"type": "string"}}

START_BODY = _object_schema(
    {
        "director_request": {
            "type": "string",
            "description": "User's normal-language scene brief: who is present, what should happen, what is forbidden. Do not ask the user to write JSON.",
        },
        "draft_id": {"type": "string", "description": "Optional stable id for this scene draft."},
        "title": {"type": "string", "description": "Optional human title for the scene draft."},
        "characters": array_string,
        "character_ids": array_string,
        "files_to_read": array_string,
        "file_paths": array_string,
    },
    required=["director_request"],
)

ADD_CONTEXT_BODY = _object_schema(
    {
        "addition": {
            "type": "string",
            "description": "User's normal-language request to add more context, e.g. 'добавь прошлое Ирэя' or 'подтяни отношения Ирэя и Акиры'.",
        },
        "files_to_read": array_string,
        "file_paths": array_string,
    },
    required=["addition"],
)

REWRITE_BODY = _object_schema(
    {
        "revision_request": {
            "type": "string",
            "description": "User's normal-language editing instruction for the current scene draft.",
        },
        "scene_text": {"type": "string", "description": "Optional current scene text to store before rewriting."},
        "draft_text": {"type": "string", "description": "Alias for scene_text."},
    },
)

SAVE_BODY = _object_schema(
    {
        "scene_text": {"type": "string", "description": "Scene text approved or being saved as a draft version."},
        "draft_text": {"type": "string", "description": "Alias for scene_text."},
        "text": {"type": "string", "description": "Alias for scene_text."},
        "note": {"type": "string", "description": "Optional note about this saved version."},
    },
    required=["scene_text"],
)

VALIDATE_BODY = _object_schema(
    {
        "scene_text": {"type": "string", "description": "Scene text to validate. If omitted, the API validates the latest saved version."},
        "draft_text": {"type": "string", "description": "Alias for scene_text."},
    },
)


def director_openapi_actions() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Akira 1206 Director Mode Actions",
            "version": DIRECTOR_RUNTIME_VERSION,
            "description": (
                "Director/scriptroom API. The user writes normal text. GPT uses these actions to build a scene-draft packet, add context on request, rewrite, validate, and save drafts. "
                "Live-game actions such as processTurn/applyTurnResult are intentionally not exposed in this schema."
            ),
        },
        "servers": [{"url": base.BASE_URL.rstrip("/")}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "summary": "Health check for the Railway service.",
                    "responses": {"200": _response("OK")},
                }
            },
            "/api/director/drafts/start": {
                "post": {
                    "operationId": "startDirectorDraft",
                    "summary": "Start a script draft from the user's normal-language scene brief.",
                    "description": "Use this when the user asks to write/sassemble a scene in Director Mode. Do not use processTurn.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": START_BODY}}},
                    "responses": {"200": _response("Director writer packet")},
                }
            },
            "/api/director/drafts/{draft_id}/context/add": {
                "post": {
                    "operationId": "addDirectorContext",
                    "summary": "Add more context/files to an existing director draft by normal-language instruction.",
                    "parameters": [_draft_path_param()],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": ADD_CONTEXT_BODY}}},
                    "responses": {"200": _response("Updated writer packet")},
                }
            },
            "/api/director/drafts/{draft_id}/rewrite": {
                "post": {
                    "operationId": "rewriteDirectorDraft",
                    "summary": "Prepare a rewrite packet for the current scene draft based on the user's normal-language revision request.",
                    "parameters": [_draft_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": REWRITE_BODY}}},
                    "responses": {"200": _response("Rewrite writer packet")},
                }
            },
            "/api/director/drafts/{draft_id}/validate": {
                "post": {
                    "operationId": "validateDirectorDraft",
                    "summary": "Validate a scene draft against the director brief and Stage 1 safety/canon checks.",
                    "parameters": [_draft_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": VALIDATE_BODY}}},
                    "responses": {"200": _response("Validation result")},
                }
            },
            "/api/director/drafts/{draft_id}/save": {
                "post": {
                    "operationId": "saveDirectorDraft",
                    "summary": "Save a scene draft version in Director Mode storage.",
                    "parameters": [_draft_path_param()],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": SAVE_BODY}}},
                    "responses": {"200": _response("Saved draft version")},
                }
            },
            "/api/director/drafts/{draft_id}": {
                "get": {
                    "operationId": "getDirectorDraft",
                    "summary": "Get the current director draft snapshot and writer packet.",
                    "parameters": [_draft_path_param()],
                    "responses": {"200": _response("Director draft snapshot")},
                }
            },
        },
    }


def _replace_openapi_actions_route() -> None:
    # production_runtime_patch already registered /openapi-actions.json. Remove it
    # so Custom GPT receives the Director Mode schema, not the live-game schema.
    app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != "/openapi-actions.json"]

    @app.get("/openapi-actions.json", include_in_schema=False)
    def director_openapi_actions_route() -> dict[str, Any]:
        return director_openapi_actions()

    def custom_openapi() -> dict[str, Any]:
        return director_openapi_actions()

    app.openapi_schema = None
    app.openapi = custom_openapi  # type: ignore[assignment]
    app.version = DIRECTOR_RUNTIME_VERSION  # type: ignore[attr-defined]


_replace_openapi_actions_route()
