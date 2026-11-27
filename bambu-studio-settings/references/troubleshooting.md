# Troubleshooting

## A new user preset does not show in Bambu Studio
- Restart Bambu Studio - it reads presets only at startup.
- Wrong folder: it must be `<BambuStudio user folder>/user/<user_id>/<type>/` for the account that is signed in
  (signed out = `user/default`). `list_presets.py --user` shows what is where.
- The base is not compatible with the selected printer: a process preset only shows for the printers its base's
  `compatible_printers` lists. Pick the base with `list_presets.py --printer "<model> <nozzle>"`.
- `inherits` misspelt or names a preset missing from this Studio version: the app skips the preset. Re-run
  `make_preset.py` (it checks the base exists).
- JSON broken (hand edits): validate with `python -m json.tool file.json`.

## A preset appeared on another computer / disappeared
- User presets sync through the Bambu account. A preset written while signed in uploads on next start; deleting
  one in the app deletes it from the cloud. Do not delete preset files by hand while the app is running.

## A per-object setting is not applied
- The key is not an object-level setting in Bambu Studio (machine, filament, prime tower, sequence settings are
  plate-only); it is ignored silently. Set it plate-wide or make a separate plate.
- The rule did not match: `tune_project.py` prints "rule ... matched no object". Object names are the names in
  the app's object list (often the STL file name, including `.stl`). Regexes are case-insensitive.
- Check what slicing really did: `slice_check.py` shows each object's z levels (layer height) from the G-code.

## Bambu Studio refuses to slice: different layer heights / prime tower
- A plate with the prime tower on and 2+ filaments needs one layer height for every object on it. Either give all
  objects that plate's height (and record that the tested parts are now printed at a different height), move the
  odd object to its own plate, or, for a single-filament plate, turn the prime tower off.

## "Filaments are not compatible with the plate type" / wrong bed temperature
- The filament has a bed temperature of 0 for the plate `curr_bed_type` names (PETG on Cool Plate, for example),
  or the project has no `curr_bed_type` and the CLI fell back to Cool Plate. Set `curr_bed_type` to the plate on
  the printer (ask the user which one): `tune_project.py in.3mf out.3mf --set "curr_bed_type=<plate>"`.
- `verify_project.py` prints the plate and the bed temperature each filament gets; a wrong plate does not fail,
  it just prints at another plate's temperature. See "Build plate" in `settings-cookbook.md`.

## CLI slice fails or crashes
- Multi-filament projects often crash the CLI (exit codes like 3221225477 / -1073741819 on Windows). Verify with
  `verify_project.py` and slice once in the app.
- `G-code conflicts detected after slicing ... try moving the wipe tower further from other models`: the CLI checks
  for toolpath collisions (it also fires for tree supports between objects placed close together). Spread objects
  out or move the prime tower; check support settings.
- `The input files to the slicer are not found`: a path problem - pass absolute paths (`slice_check.py` does).
- STL mode needs complete machine/process/filament JSONs (full presets, e.g. exported from the app); a user preset
  file with only overrides is not enough for `--load-settings`.
- Results missing from `--outdir`: `slice_check.py` never replaces a file there; if any result file already
  exists it copies none of them and lists them. Use a new folder, or `--keep` to keep the temporary work folder.
- `archive refused`: the project has a zip layout Bambu Studio does not write ("the zip structure is not one
  Bambu Studio writes": a zip comment, bytes before or after the archive, a second central directory, zip64
  records that disagree with each other, encryption, a compression method other than deflate), two entries with one name, a settings file named in
  another case or with a backslash, a non-ASCII name outside `3D/` and `Auxiliaries/`, settings that do not
  parse, nesting deeper than 256 levels, more than 64 MB of settings and metadata, entries that expand more than
  200 times (each, the settings and metadata together, or the whole archive), more than 200,000 XML elements,
  200,000 JSON objects and arrays or 1,000,000 strings in all the settings and metadata, XML markup over
  64 KB, more than 65,536 characters of distinct XML names in one entry, or an entry whose data is longer or shorter than its size says. Bambu Studio never writes such files; the scripts refuse them because
  Bambu Studio could read their settings differently, or because reading them would take too long or too much
  memory. If you trust the file, open it in Bambu Studio, save it as a new project without slicing, and use that
  copy. A file that is not a zip at all is refused too ("not a .3mf project"); re-saving does not apply there.
- `geometry not checked: the project has more than ...`: the .model files hold more than 100,000 objects or
  components, 10,000 build items, 10 million vertices, 20 million triangles, 32 million elements (1 million
  that are not vertices or triangles) or 16 million characters of ids, names and transforms. The settings
  checks still ran.
- `... and N more like this`: verify prints the same PROBLEM or note at most 20 times, then counts the rest
  (`"more"` in `--json`).
- `slice_check.py` does not search the current folder for the executable (a downloaded folder could hold a fake
  one). Pass `--exe` with the full path, or set `BAMBU_STUDIO_EXE`.
- No `result.json` and no output: wrong executable path (`--exe`, or set `BAMBU_STUDIO_EXE`). On macOS the binary
  is `/Applications/BambuStudio.app/Contents/MacOS/BambuStudio`.

## Objects outside the bed / wrong plate
- `verify_project.py` reports world boxes per plate after removing the plate grid offset. If an object is
  "outside" by about one bed width plus 20%, it is on a different plate than `model_settings.config` says.
- Two-nozzle printers: a multi-color object must be inside the area both nozzles reach (H2D: x 25..325).

## Colors look wrong when opening the project
- `filament_colour` in `project_settings.config` was not set (the app shows defaults) or the number of filaments
  in the config does not match the highest `extruder` slot used. `verify_project.py` checks both.

## The fit changed after "just improving the look"
- Layer height, wall count, `wall_sequence`, `elefant_foot_compensation`, seam/scarf settings and top layers can all
  move a fit. Re-check against the settings the part was tested at (see the cookbook's "Recording" section).
