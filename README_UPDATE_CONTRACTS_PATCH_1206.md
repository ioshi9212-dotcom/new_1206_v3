# UPDATE CONTRACTS PATCH 1206

Этот patch добавляет правила применения `proposed_updates` после сцены.

## Что добавлено

```txt
state/update_contracts/
  turn_update_pipeline_1206.json
  proposed_updates_validation_1206.json
  character_memory_patch_rules_1206.json
  relationship_pair_patch_rules_1206.json
  scene_state_patch_rules_1206.json

api_contracts/
  apply_turn_result.example.json
```

## Главная логика

ChatGPT не меняет state напрямую. Он возвращает `proposed_updates`.
API проверяет обновления, отклоняет опасные, сжимает безопасные и применяет через `apply_turn_result`.

## Что нельзя применять автоматически

- hidden/past как обычное знание;
- романтику/доверие/вражду без сценического основания;
- знания имени/личности без источника;
- появление персонажа без причины;
- изменения в `characters/<id>/*.yaml` из обычного хода сцены;
- большие пересказы вместо коротких атомарных фактов.

## Что этот patch НЕ трогает

```txt
characters/
state/character_memory/*.json
state/relationship_pairs/*.json
calendar/
canon_lore/
gpt/
app/
Railway
```
