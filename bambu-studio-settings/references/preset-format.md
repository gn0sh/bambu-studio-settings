# Bambu Studio preset format

## Where presets live

- **System presets** ship with the app: `<install>/resources/profiles/BBL/{machine,process,filament}/*.json`
  (Windows `C:\Program Files\Bambu Studio\resources\profiles`, macOS
  `/Applications/BambuStudio.app/Contents/Resources/profiles`). Updated copies may also be downloaded into
  `<BambuStudio user folder>/system/`. `BBL.json` next to the `BBL` folder has the profile version and the list
  of every preset file.
- **User presets**: `<BambuStudio user folder>/user/<user_id>/{process,filament,machine}/<name>.json` plus
  `<name>.info`. `<user_id>` is the signed-in Bambu account's numeric id; `default` when signed out.
  Filament presets created with "Create filament" (custom vendor) go in `filament/base/`.
- **Project-embedded presets**: a project .3mf carries the full resolved settings in
  `Metadata/project_settings.config`; opening it can offer to save them as user presets.

Bambu Studio reads presets when it starts. Files added while it runs appear after a restart.

## System preset files

```json
{
    "type": "process",
    "name": "0.16mm Optimal @BBL X1C",
    "inherits": "fdm_process_single_0.16",
    "from": "system",
    "setting_id": "GP003",
    "instantiation": "true",
    "compatible_printers": ["Bambu Lab X1 Carbon 0.4 nozzle", "Bambu Lab P1S 0.4 nozzle", "..."],
    "...": "only the keys that differ from the parent"
}
```

- `inherits` chains down to internal templates (`fdm_process_common` ...); `instantiation: "false"` marks a
  template that is not offered in the app and must not be used as a user preset's base.
- `setting_id` (GPxxx process, GFxxx filament, GMxxx machine) is what a user preset's `.info` calls `base_id`.
- `compatible_printers` (process and filament) lists machine preset names: that is how the app decides which
  presets a printer shows. `list_presets.py` resolves the chain and reads it for you.

## User preset .json

Only the overrides, plus identity keys. Example (process):

```json
{
    "from": "User",
    "inherits": "0.16mm Balanced Quality @BBL H2D",
    "name": "Photo print H2D",
    "print_extruder_id": ["1", "1", "1", "2", "2", "2", "2"],
    "print_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow", "..."],
    "print_settings_id": "Photo print H2D",
    "seam_position": "back",
    "top_shell_layers": "5",
    "version": "2.9.0.89",
    "wall_loops": "3"
}
```

- `inherits`: the exact system preset name. User presets inherit one level from a system preset (not from another
  user preset).
- `from`: `"User"`.
- Identity key: `print_settings_id` (process), `filament_settings_id` (filament, a one-element list),
  `printer_settings_id` (machine) - equal to `name`.
- `version`: the profile version in dotted form (`02.09.00.89` in `BBL.json` -> `2.9.0.89`).
- `print_extruder_id` / `print_extruder_variant` (process) and `filament_extruder_variant` (filament) are written
  by the app for the printer's extruder variants; `make_preset.py` copies them from the base. List-valued settings
  (speeds, accelerations, temperatures) have one entry per variant - give all entries the same value unless you
  mean otherwise.
- Keys are sorted alphabetically when Bambu Studio writes the file. Values are strings.

## User preset .info

```
sync_info = create
user_id = <user_id>
setting_id =
base_id = GP174
updated_time = 1767225600
```

- `base_id`: the base system preset's `setting_id`.
- `setting_id`: assigned by Bambu's cloud when the preset is uploaded (for example `PPUS...` for process and `PFUS...` for filament presets).
  Leave it empty for a new preset.
- `sync_info`: presets already in the cloud show an empty value. `make_preset.py` writes `create` for a new one
  (the value the app itself writes when you save a new preset: Bambu Studio source,
  `src/slic3r/GUI/Tab.cpp`, `Tab::save_preset`, checked Nov 2026); pass `--sync-info ""` to write it empty. Either way,
  when the app runs signed in it uploads presets that have no cloud `setting_id` - so a new preset reaches the
  user's account and their other machines. Tell the user before writing one.
- `updated_time`: Unix seconds.
- `user_id`: the folder's account id (empty for `default`).

## Safe practice

- Dry-run first (`make_preset.py` without `--write`) and show the user the file.
- Use a new name; do not overwrite or "improve" an existing user preset unless asked. If asked to change one,
  copy the original files somewhere first.
- Deleting a preset file while the app syncs can delete it from the cloud too - leave deletion to the user, in
  the app.
