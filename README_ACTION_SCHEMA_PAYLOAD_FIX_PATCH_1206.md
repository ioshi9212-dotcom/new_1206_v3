# ACTION_SCHEMA_PAYLOAD_FIX_PATCH_1206

Patch-only fix for Custom GPT Actions schema.

## Why this patch exists

Backend 0.3.189 already rejects stale empty turns, but the custom OpenAPI schema still exposed request bodies as generic empty objects. Because of that Custom GPT Actions generated functions that accepted only the path `session_id` and rejected fields like `player_input` with `UnrecognizedKwargsError`.

## What changes

- Updates runtime version to `0.3.190-v3-actions-payload-schema`.
- Keeps the 0.3.189 knowledge-boundary / maintenance / render behavior.
- Exposes explicit requestBody properties for:
  - `processTurn.player_input`
  - `requestContextSlice.player_input`
  - `requestContextSlice.scene_plan`
  - `requestContextSlice.character_requests`
  - `applyTurnResult.proposed_updates`
  - `applyTurnResult.visible_scene_text / final_scene_text / scene_text`
- Does not add new lore/rules.
- Does not change characters or story files.

## After deploy

1. Check `/health` and confirm version:
   `0.3.190-v3-actions-payload-schema`
2. Re-import `https://new1206v3.up.railway.app/openapi.json` into the new Custom GPT Actions.
3. Test a real turn after the start scene. `processTurn` must accept `player_input`.
