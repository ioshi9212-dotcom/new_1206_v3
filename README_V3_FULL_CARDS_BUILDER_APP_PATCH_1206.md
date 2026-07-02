# V3 Full Cards Builder App Patch 1206

Patch-only слой для Railway/API-репы `1206_v2-main`.

## Что добавляет

- `app/v3_full_cards_scene_contract_runtime_patch.py` — новый `/api/v2/sessions/{session_id}/scene-contract`.
- `app/v3_apply_turn_result_runtime_patch.py` — новый безопасный `/api/v1/sessions/{session_id}/apply-turn-result`.
- `app/production_runtime_patch.py` — только импортирует два новых override-файла последними.

## Главное правило

Персонажи берутся из полной v3-структуры:

```txt
characters/<id>/main.yaml
characters/<id>/character.yaml
characters/<id>/knowledge.yaml
state/character_memory/<id>.json
```

`past.yaml` попадает в контракт только по триггеру прошлого.

## Что больше не используется

```txt
runtime/characters/
state/character_knowledge/
state/relationships.json как основной relationship source
```

## Важно для деплоя

Этот patch не содержит контент-слой. В Railway/API-репе рядом с `app/` должны быть уже добавлены папки v3-контента:

```txt
characters/
state/character_memory/
state/relationship_pairs/
api_contracts/
calendar/
canon_lore/
gpt/
```

Если этих папок нет в API-репе, builder будет живой, но карточки не найдёт.

## Проверка после деплоя

1. `/health` должен показать версию около `0.3.180` или выше.
2. `/api/v2/sessions/<id>/debug/context-audit` должен вернуть:
   - `runtime_characters_used: false`
   - `character_knowledge_legacy_path_used: false`
   - `source_files_by_character` с путями `characters/<id>/...`
3. В `scene_contract.scene_contract.loaded_characters` должны быть `main_yaml`, `character_yaml`, `knowledge_yaml`, `character_memory`.
