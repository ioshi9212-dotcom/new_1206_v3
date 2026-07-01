# Scene Context Assembly Layer 1206

Этот ZIP описывает, как Railway/API должен собирать `scene_contract` для 1206.

## Зачем он нужен

Раньше контекст легко раздувался:

- Акира грузилась всегда;
- все отношения Акиры попадали в сцену;
- NPC/календарь/hidden lore могли смешиваться;
- GPT получал слишком много лишнего и начинал писать удобных персонажей, спойлеры и телепатию.

Новый слой фиксирует другую систему:

```txt
текущий POV + реальные участники сцены + нужные пары + нужный NPC/location context + короткие правила вывода
```

## Что внутри

```txt
state/context_loading/
  master_context_loading_rules_1206.json
  scene_contract_assembly_rules_1206.json
  pov_active_character_loading_matrix_1206.json
  relationship_pair_selection_rules_1206.json
  location_privacy_context_rules_1206.json
  npc_context_selection_rules_1206.json

gpt/runtime/
  scene_contract_requirements_1206_ru.md
  scene_contract_forbidden_context_1206_ru.md

docs/examples/
  scene_contract_akira_room.json
  scene_contract_public_cafeteria.json
  scene_contract_haru_pov_without_akira.json
  scene_contract_haru_raiden_without_akira.json
  scene_contract_akira_raiden_tree.json

PATCH_PLAN_SCENE_CONTRACT_BUILDER_1206.md
```

## Главное правило

Builder/сборщик — это Railway/API-код, который выбирает данные.

Prompt — это инструкция GPT, как пользоваться уже собранным `scene_contract`.

## Следующий шаг

После этого слоя логично делать кодовый патч для:

```txt
app/compact_scene_contract_runtime_patch.py
app/scene_memory_contract_runtime_patch.py
app/production_runtime_patch.py
```
