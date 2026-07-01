# Patch Plan: Scene Contract Builder 1206

Это план будущего кодового патча для Railway/API. Этот ZIP пока не меняет runtime-код, а фиксирует архитектуру.

## Главная цель

Переписать сборку `scene_contract`, чтобы она стала POV-aware, compact, memory-safe и не Акира-центричной.

## Файлы, которые вероятно нужно править

```txt
app/compact_scene_contract_runtime_patch.py
app/scene_memory_contract_runtime_patch.py
app/production_runtime_patch.py
app/prompt_builder.py        # fallback/legacy only
state/context_loading_rules_1206.json  # заменить/дополнить новыми rules
```

## Критичная правка 1: убрать auto-Akira

Найти логику вида:

```python
if "akira" not in scene_ids:
    scene_ids.insert(0, "akira")

if "akira" not in active_ids:
    active_ids.insert(0, "akira")
```

Заменить на POV-aware функцию:

```python
def should_load_akira(frame, participants, scene_goal, last_player_input):
    return (
        frame.get("pov_character_id") == "akira"
        or "akira" in participants.any_active_present_addressed_observed()
        or scene_goal_directly_mentions("akira")
        or last_player_input_controls("akira")
        or direct_relationship_pair_required("akira")
    )
```

## Критичная правка 2: relationship pair selection

Не использовать:

```python
if any(cid in pair_key for cid in scene_ids):
    load(pair_key)
```

Использовать:

```python
def should_load_pair(pair, scene_ids, direct_targets, scene_goal_pairs, open_thread_pairs):
    a, b = pair.participants
    return (
        (a in scene_ids and b in scene_ids)
        or pair.id in direct_targets
        or pair.id in scene_goal_pairs
        or pair.id in open_thread_pairs
    )
```

## Критичная правка 3: character_memory вместо character_knowledge как dynamic layer

Новый путь:

```txt
state/character_memory/<id>.json
```

Старый путь `state/character_knowledge/<id>.json` может быть legacy fallback, но не основным источником.

## Критичная правка 4: NPC context by location privacy

Добавить режим:

```python
npc_context_mode = select_npc_context_mode(location_privacy, location_type, scene_need)
```

- private: none/light
- quiet_open: light/medium
- public: medium/full
- functional: function_based

## Критичная правка 5: output_contract в scene_contract

Добавить digest из:

```txt
gpt/scene_output/scene_writing_format_1206_ru.md
gpt/scene_output/scene_footer_rules_1206_ru.md
gpt/scene_output/scene_quality_control_1206_ru.md
```

Но не вставлять огромные файлы полностью. Лучше короткий digest/loaded rule ids.

## Критичная правка 6: state write contract

scene_contract должен сообщать GPT, что после сцены applyTurnResult должен сохранить:

- кто что увидел/услышал
- новые known/unknown/assumptions/mistakes
- изменения relationship_pairs
- protected_moments
- перемещения/предметы/травмы
- кто услышал важную реплику в публичном месте
- pending interruption, если NPC перебил уход вопросом

## Проверочные кейсы после патча

1. POV Haru без Акиры → Акира не загружена.
2. POV Haru + Raiden → загружена только пара `raiden__haru`.
3. Акира в комнате одна → нет полного NPC rules, нет всех отношений.
4. Акира в столовой с Мики и Райденом → загружены только активные пары.
5. NPC задаёт вопрос, когда игрок написал `(выйти)` → сцена останавливается на выборе, не пишет “Акира решила не отвечать”.
6. Hidden lore не попадает в POV/реплики.
