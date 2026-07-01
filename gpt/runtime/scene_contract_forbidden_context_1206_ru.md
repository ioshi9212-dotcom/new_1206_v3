# Scene Contract Forbidden Context 1206

Этот файл фиксирует, что нельзя класть в `scene_contract` без причины.

## Нельзя грузить автоматически

```yaml
forbidden_by_default:
  - полный календарь месяца
  - будущие события календаря
  - hidden lore / author-only truth
  - все relationship_pairs Акиры
  - все character_memory
  - все карточки characters/
  - весь relationships.json как основной источник
  - все NPC/session NPC
  - прошлые сцены целиком
  - большие raw yaml/documents вместо компактных slices
```

## Нельзя делать знанием NPC

```yaml
not_npc_knowledge:
  - календарные планы
  - правила сцены
  - правила footer
  - правила сборщика
  - hidden lore
  - будущие события
  - чужие мысли
  - state другого персонажа без сценического источника
```

## Нельзя грузить Акиру всегда

Акира загружается только если она текущий POV, присутствует, говорит, действует, наблюдается, упомянута как цель сцены или нужна relationship_pair.

Плохо:

```yaml
scene_ids:
  - akira
  - haru
  - raiden
```

если сцена на самом деле POV Хару + Райден без Акиры.

Хорошо:

```yaml
scene_ids:
  - haru
  - raiden
relationship_pairs:
  - raiden__haru
```

## Нельзя грузить все пары по одному имени

Плохо: `if cid in pair_id -> load pair`, потому что `akira` подтянет все `akira__*`.

Нужно: pair грузится только если оба участника активны или есть прямой scene_goal/open_thread по этой паре.

## Нельзя использовать hidden relationship как реплику

Скрытая связь/перерождение/hidden bond может влиять на странность реакции, но не становится текстом POV или знанием персонажа без источника.
