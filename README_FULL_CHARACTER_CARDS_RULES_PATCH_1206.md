# Full character cards only — rules patch 1206

Это patch-only ZIP. Не полный репозиторий.

Что обновляет:

- `state/context_loading/character_memory_loading_rules_1206.json`
- `gpt/scene_output/pov_context_loading_rules_1206.json`
- `canon_lore/social/npc_presence_rules_1206_ru.yaml`
- `AGENTS.md`
- `README.md`

Что НЕ трогает:

- `characters/`
- `state/character_memory/<id>.json`
- `state/relationship_pairs/`
- `calendar/`
- карточки персонажей и их знания

Смысл правки:

- персонажи читаются из полных карточек `characters/<id>/main.yaml`, `character.yaml`, `knowledge.yaml`;
- `state/character_memory/<id>.json` остаётся динамической памятью;
- `relationship_pairs` грузятся только когда пара реально нужна;
- `past.yaml` грузится только по триггеру прошлого;
- отдельные короткие summary-файлы персонажей не создаются, чтобы не уплощать характер.
