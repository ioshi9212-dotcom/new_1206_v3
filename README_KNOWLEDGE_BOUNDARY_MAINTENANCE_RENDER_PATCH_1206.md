# 1206 v3 — knowledge boundary + maintenance + render contract patch

Version after deploy: `0.3.189-v3-knowledge-maintenance-render`.

## What this patch fixes

1. Speaking NPCs always receive knowledge context:
   - `characters/<id>/knowledge.yaml`
   - `state/character_memory/<id>.json`
   - `knowledge_boundary` with `can_say_as_fact`, `believes_or_assumes`, `does_not_know`, `forbidden_as_fact`, seen/heard facts.

2. The player input is not lost:
   - `processTurn` accepts `player_input` and `user_input`.
   - Empty ordinary turns are rejected instead of writing a scene from stale `начнем` context.
   - `requestContextSlice` accepts `player_input` and `user_input` and reports input consistency.

3. Relationships are no longer empty when `relationship_pair_ids` exist.
   - Added `jun__emma.json` and `jun__irey.json` for start-house pressure.

4. Temporary knowledge/beliefs are restored for start-scene NPCs:
   - Irey thinks Akira may remember him; he does not know amnesia, note, East Sector, Akatsumi documents.
   - Emma does not know Jun's name, note, East Sector, Akatsumi documents, amnesia, Jun/Akira connection.
   - Jun does not know Emma/Irey names, exact goals, sender, or how much they know.

5. Maintenance runtime:
   - `state/story_lines.json` stores `turn_counter`.
   - Recovery audit due every 10 turns.
   - Compaction cleanup due every 15 turns with offset 12.
   - Preflight/context include maintenance status and deeper recent history when due.

6. Final render contract:
   - Adds `gpt/scene_output_contract_1206.json`.
   - `final_render_contract` is the last writer-facing block inside `context_slice`.
   - Requires header, dialogue format `**Имя** — реплика.`, lower blocks, and unknown-name labels.

## How to install

Upload the contents of this ZIP over the current repo. Do not delete existing folders manually.

After Railway redeploy:

1. Check `/health`.
2. Expected version: `0.3.189-v3-knowledge-maintenance-render`.
3. Re-import OpenAPI in the new Custom GPT.

## Important

This is not a new rules pile. It makes runtime context slicing carry the actual temporary knowledge boundary for speaking NPCs.
