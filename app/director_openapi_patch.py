"""Custom GPT Actions schema for Director Mode only.

This patch intentionally exposes only Director Mode actions in /openapi-actions.json.
The old live-game FastAPI routes can remain registered, but Custom GPT should not see
processTurn/applyTurnResult/context chunk actions in this schema.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Request

from app import compact as base
from app.compact import app
from app.director_runtime_patch import DIRECTOR_RUNTIME_VERSION


def _schema_obj(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return schema


def _string_array(description: str = "") -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
    if description:
        schema["description"] = description
    return schema


def _json_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"type": "object"}
            }
        },
    }


def _draft_id_param() -> dict[str, Any]:
    return {
        "name": "draft_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
        "description": "Director draft id returned by startDirectorDraft.",
    }


def _request_body(schema: dict[str, Any], required: bool = True) -> dict[str, Any]:
    return {
        "required": required,
        "content": {
            "application/json": {
                "schema": schema
            }
        },
    }


START_DIRECTOR_SCHEMA = _schema_obj(
    {
        "director_request": {
            "type": "string",
            "description": "User's normal-language scene brief. Include who is in the scene, what should happen, tone, and restrictions. The user must not write JSON manually.",
        },
        "draft_id": {
            "type": "string",
            "description": "Optional stable id for this draft. Usually omit unless the user names one.",
        },
        "title": {
            "type": "string",
            "description": "Optional scene title.",
        },
        "characters": _string_array("Optional character names or ids explicitly requested by the user."),
        "files_to_read": _string_array("Optional exact repository file paths, only when the user explicitly names paths."),
    },
    required=["director_request"],
)

ADD_CONTEXT_SCHEMA = _schema_obj(
    {
        "addition": {
            "type": "string",
            "description": "User's normal-language request to add context, for example: 'добавь прошлое Ирэя' or 'подтяни отношения Ирэя и Акиры'.",
        },
        "files_to_read": _string_array("Optional exact repository file paths, only when the user explicitly names paths."),
    },
    required=["addition"],
)

REWRITE_SCHEMA = _schema_obj(
    {
        "revision_request": {
            "type": "string",
            "description": "User's normal-language editing instruction for this draft.",
        },
        "scene_text": {
            "type": "string",
            "description": "Current scene text to store before preparing the rewrite packet, when available.",
        },
    },
    required=["revision_request"],
)

VALIDATE_SCHEMA = _schema_obj(
    {
        "scene_text": {
            "type": "string",
            "description": "Scene text to validate. If omitted, the API validates the latest saved version when available.",
        },
    }
)

SAVE_SCHEMA = _schema_obj(
    {
        "scene_text": {
            "type": "string",
            "description": "Scene text approved by the user or being saved as a draft version.",
        },
        "note": {
            "type": "string",
            "description": "Optional note about this saved version.",
        },
    },
    required=["scene_text"],
)


def _public_base_url(request: Request | None = None) -> str:
    """Return a public HTTPS base URL suitable for GPT Actions.

    Prefer explicit env values, but fall back to the incoming request host so the
    schema does not accidentally publish http://localhost:8000 after Railway deploy.
    """
    for key in ("PUBLIC_BASE_URL", "RAILWAY_PUBLIC_DOMAIN"):
        value = os.getenv(key) or ""
        value = value.strip().rstrip("/")
        if value:
            if not value.startswith(("http://", "https://")):
                value = "https://" + value
            return value
    if request is not None:
        return str(request.base_url).rstrip("/")
    return str(base.BASE_URL or "").rstrip("/") or "https://example.com"


def director_openapi_actions(server_url: str | None = None) -> dict[str, Any]:
    url = (server_url or _public_base_url()).rstrip("/")
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Akira 1206 Director Mode Actions",
            "version": DIRECTOR_RUNTIME_VERSION,
            "description": "Director/scriptroom API. User writes normal text. GPT builds scene draft context, adds context on request, rewrites, validates, and saves drafts. This is not live gameplay.",
        },
        "servers": [{"url": url}],
        "paths": {
            "/api/director/drafts/start": {
                "post": {
                    "operationId": "startDirectorDraft",
                    "summary": "Start a Director Mode scene draft from the user's normal-language brief.",
                    "description": "Use when the user asks to write, collect, assemble, or start a scene draft. Do not use live-game actions.",
                    "requestBody": _request_body(START_DIRECTOR_SCHEMA, required=True),
                    "responses": {"200": _json_response("Director writer packet")},
                }
            },
            "/api/director/drafts/{draft_id}/context/add": {
                "post": {
                    "operationId": "addDirectorContext",
                    "summary": "Add more context to an existing Director Mode draft.",
                    "description": "Use when the user says to add a file, character context, past, relationships, lore, calendar, or other extra context.",
                    "parameters": [_draft_id_param()],
                    "requestBody": _request_body(ADD_CONTEXT_SCHEMA, required=True),
                    "responses": {"200": _json_response("Updated writer packet")},
                }
            },
            "/api/director/drafts/{draft_id}/rewrite": {
                "post": {
                    "operationId": "rewriteDirectorDraft",
                    "summary": "Prepare a rewrite packet for a scene draft.",
                    "description": "Use when the user asks to rewrite or correct the current scene draft.",
                    "parameters": [_draft_id_param()],
                    "requestBody": _request_body(REWRITE_SCHEMA, required=True),
                    "responses": {"200": _json_response("Rewrite writer packet")},
                }
            },
            "/api/director/drafts/{draft_id}/validate": {
                "post": {
                    "operationId": "validateDirectorDraft",
                    "summary": "Validate a scene draft against Director Mode restrictions.",
                    "description": "Use when the user asks to check errors, canon leaks, forbidden terms, unapproved characters, or wrong tone.",
                    "parameters": [_draft_id_param()],
                    "requestBody": _request_body(VALIDATE_SCHEMA, required=False),
                    "responses": {"200": _json_response("Validation result")},
                }
            },
            "/api/director/drafts/{draft_id}/save": {
                "post": {
                    "operationId": "saveDirectorDraft",
                    "summary": "Save a scene draft version.",
                    "description": "Use only when the user asks to save or approve a draft version.",
                    "parameters": [_draft_id_param()],
                    "requestBody": _request_body(SAVE_SCHEMA, required=True),
                    "responses": {"200": _json_response("Saved draft version")},
                }
            },
            "/api/director/drafts/{draft_id}": {
                "get": {
                    "operationId": "getDirectorDraft",
                    "summary": "Get the current Director Mode draft snapshot.",
                    "description": "Use when the user asks what context is loaded, what draft is current, or to show the current draft packet.",
                    "parameters": [_draft_id_param()],
                    "responses": {"200": _json_response("Director draft snapshot")},
                }
            },
        },
    }


def _replace_openapi_actions_route() -> None:
    # production_runtime_patch already registered /openapi-actions.json. Remove it
    # so Custom GPT receives Director Mode schema only.
    app.router.routes = [route for route in app.router.routes if getattr(route, "path", None) != "/openapi-actions.json"]

    @app.get("/openapi-actions.json", include_in_schema=False)
    def director_openapi_actions_route(request: Request) -> dict[str, Any]:
        return director_openapi_actions(_public_base_url(request))

    def custom_openapi() -> dict[str, Any]:
        return director_openapi_actions(_public_base_url(None))

    app.openapi_schema = None
    app.openapi = custom_openapi  # type: ignore[assignment]
    app.version = DIRECTOR_RUNTIME_VERSION  # type: ignore[attr-defined]


_replace_openapi_actions_route()
