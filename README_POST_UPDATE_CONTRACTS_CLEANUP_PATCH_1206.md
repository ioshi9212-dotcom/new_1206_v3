# POST UPDATE CONTRACTS CLEANUP PATCH 1206

Patch-only cleanup after adding update contracts.

## Files included

- api_contracts/README_API_CONTRACTS_1206.md
- characters/jun/knowledge.yaml
- canon_lore/hidden/99_hidden_lore_author_only_ru.yaml

## What changed

- Removed the obsolete `runtime/characters/` mention from the API contracts README.
- Renamed Jun's `runtime_knowledge` block to `dynamic_knowledge`.
- Renamed hidden lore status from `not_for_runtime_default` to `author_only_not_for_default_scene_context`.

## Not included

This patch does not touch:
- state/character_memory/*.json
- state/relationship_pairs/*.json
- other character cards
- app / Railway code
