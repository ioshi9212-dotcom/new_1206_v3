# State + Relationship Memory Layer 1206 — v2 migrated

Этот ZIP обновляет предыдущий слой памяти. В него перенесены разбросанные знания из старой репы:

- `state/character_knowledge/*.json`
- `characters/*/knowledge.yaml`
- `state/initial_relationships_1206.json`
- `state/relationship_score_panel.json`
- `state/raiden_akira_dynamic_rules.json`
- `canon/relationships/akira_raiden_hidden_bond.yaml`
- `canon/relationships/akira_raiden_memory_fragments.yaml`
- `state/relationship_memory_rules_1206.json`

## Важно

1. Старые знания не потеряны: они лежат в `migrated_static_knowledge_snapshot` и `migrated_dynamic_state_snapshot`.
2. Это не значит, что NPC всё раскрывают. Знание ≠ раскрытие.
3. Календарь, правила сцены и маршрутные цепочки не становятся знанием NPC.
4. Вредные старые конфликты были санитизированы:
   - Рэй → Акира: семейная/защитная привязанность, не романтика.
   - Райден → Юми/Акира: Райден знает помолвку с Юми как контракт, но не связывает её с Акирой сознательно.
5. Relationship-pairs грузятся только по релевантности, не все пары Акиры сразу.

## Что делать дальше

Следующий технический этап — обновить сборщик scene_contract, чтобы он читал:

- `state/character_memory/<active_character>.json`
- `state/relationship_pairs/<relevant_pair>.json`
- `state/session_npcs.json` только по NPC-правилам

и не грузил старые `state/character_knowledge`/`relationships` целиком.
