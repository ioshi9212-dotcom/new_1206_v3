# Migration to `state/character_memory/`

Текущий стандарт динамической памяти персонажа — `state/character_memory/<id>.json`.

Старый слой `character_knowledge` больше не используется как место записи новых сценических фактов.
Статичные файлы `characters/<id>/knowledge.yaml` остаются карточками стартовых знаний персонажа, но всё новое после сцен пишется в `character_memory`.

## Правило

- `characters/<id>/knowledge.yaml` = стартовые постоянные знания / незнания персонажа.
- `state/character_memory/<id>.json` = динамическая память после сцен.
- `state/relationship_pairs/<a>__<b>.json` = динамика пары.
- Не создавать новый старый dynamic-state слой рядом с `character_memory`.
