# Turn Runtime Contract 1206 v2

## Actions sequence

### First scene

User says: `начнем`, `начнём`, `start`, `begin`.

Call:

`processTurn(session_id, player_input, mode="play", include_file_contents=true)`

If response status is `START_SCENE_EXACT_TEXT`, output exactly `scene_text` as scene.

### Normal play turn

Call:

`getTurnContract(session_id, user_input, mode="play", include_file_contents=true)`

Then call:

`getFastRenderContext(session_id, user_input, mode="play")`

Do **not** call diagnostic file chunks in normal gameplay.

Only after `getFastRenderContext` generate scene.

Then save:

`submitTurnResult(session_id, scene_id, scene_text, technical=false, state_patches={...})`

## Technical mode

If user asks about repo, Railway, volume, schema, prompt, health, files, saves, API:

- use `mode="technical"`;
- do not continue scene;
- save technical note only if needed.

## Required files rule

If a character speaks, their card must be in required files.

If scene includes hidden memory, past, rings, scars, reincarnation, Samуэль, Эхо, кайросы, or Aкира/Rайден emotional bond — corresponding hidden files must be loaded.

## Do not

- Do not generate from chat memory if API failed.
- Do not load the whole repo during gameplay; use fast context.
- Do not reveal hidden lore only because hidden file was loaded.
- Do not overwrite player control of Akira.
