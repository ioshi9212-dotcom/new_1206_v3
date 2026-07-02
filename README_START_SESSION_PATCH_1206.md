# Start Session Patch — 1206 v3

Patch-only. Do not use as full repo.

## Purpose

Fix v3 standalone session start behavior:

- A new Custom GPT chat can create a fresh session without reusing `default`.
- The command `начнем` / `начнём` / `старт` initializes the canonical first 1206 scene.
- First scene uses the 1206 v2 start scene text, adapted for v3 spelling `Акацуми`.
- Calendar/state/relationships are no longer empty at session start.
- Hidden `past.yaml` is not loaded just because ordinary words like `записка`, `документы` or `Джун` appear.

## Files in this patch

```txt
app/compact.py
app/production_runtime_patch.py
app/v3_full_cards_scene_contract_runtime_patch.py
app/v3_apply_turn_result_runtime_patch.py

scenes/start_scene.md
scenes/start_scene_logic.md

state/current_state.json
state/calendar_runtime.json
state/context_loading/past_trigger_rules_1206.json

state/relationship_pairs/akira__jun.json
state/relationship_pairs/akira__irey.json
state/relationship_pairs/akira__emma.json
```

## Apply

Upload/overwrite these files in the v3 Railway repo. Do not delete characters, state memory, relationship pairs, api_contracts, gpt, calendar, or canon_lore.

## Expected health

```json
{
  "version": "0.3.183-v3-start-session-state"
}
```

## Expected start behavior

POST `/api/v1/start` with empty body, or POST `/api/v1/sessions` without `session_id`, returns a fresh session and scene contract with:

```txt
scene_character_ids: ["akira", "jun", "irey", "emma"]
loaded_relationship_pairs: akira__jun, akira__irey, akira__emma
start_scene.exact_text_required: true
past.loaded: false for ordinary start scene
```
