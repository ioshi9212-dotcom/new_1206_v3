# Migration note: `character_knowledge` → `character_memory`

В старой структуре уже есть `state/character_knowledge/<id>.json`. Новая логика называет это точнее: `state/character_memory/<id>.json`.

Переход можно делать мягко:

1. Сборщик сначала ищет `state/character_memory/<id>.json`.
2. Если файла нет — fallback на старый `state/character_knowledge/<id>.json`.
3. При следующей записи state можно сохранить в новую схему.
4. Старые файлы не удалять резко, пока сборщик и state writer не обновлены.

Главное различие:

- `knowledge` звучит как список фактов.
- `memory` хранит не только факты, но и видел/слышал/предполагает/ошибается/скрывает/зацепился.
