# State + Relationship Memory Layer 1206 RU

Этот пакет добавляет слой памяти персонажей и отношений для 1206.

## Зачем

Чтобы сборщик контекста не тянул всё подряд, а давал GPT только нужное:

- личную память активных персонажей;
- отношения только между нужными персонажами;
- protected moments, которые нельзя потерять при чистке;
- правила загрузки и записи state.

## Структура

```yaml
state/character_memory/
  _template.json
  _index.json
  akira.json
  raiden.json
  ray.json
  miki.json
  yuna.json
  haru.json
  shiro.json
  alex.json
  jun.json
  irey.json
  emma.json

state/relationship_pairs/
  _pair_template.json
  _index.json
  akira__raiden.json
  akira__ray.json
  akira__miki.json
  akira__yuna.json
  akira__haru.json
  akira__shiro.json
  akira__alex.json
  raiden__haru.json
  miki__alex.json
  ray__yuna.json
  jun__akira.json
  jun__ray.json
  irey__akira.json
  emma__akira.json

state/context_loading/
  character_memory_loading_rules_1206.json
  relationship_pair_loading_rules_1206.json

state/update_contracts/
  every_turn_state_write_contract_1206.json
  protected_moment_rules_1206.json
```

## Главное правило загрузки

```yaml
если персонаж активен:
  грузить его character_memory

если два персонажа активны или сцена о них:
  грузить их relationship_pair

если Акира POV, но второй персонаж не в сцене и не упомянут:
  НЕ грузить все отношения Акиры
```

## Главное правило памяти

Чистка удаляет шум, но не память.

Если реплика или действие зацепили персонажа по характеру, страху, долгу, вине, привязанности, подозрению или роли — это сохраняется как `protected_moment`.

## Важная защита от спойлеров

- Календарь не становится знанием NPC.
- Runtime rules не становятся мыслями персонажа.
- Если персонаж отсутствовал, он не знает сцену.
- Если источник знания неясен, это подозрение, а не факт.

## Как подключать

1. Сборщик scene_contract определяет активных персонажей.
2. По ним грузит `state/character_memory/<id>.json`.
3. По активным парам грузит `state/relationship_pairs/<a>__<b>.json`.
4. После хода state writer обновляет:
   - character_memory;
   - relationship_pairs;
   - protected_moments;
   - предметы/локацию/состояние.

Промт править после обновления сборщика и state writer.
