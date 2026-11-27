# The filament registry (filaments.json)

One file per maker, kept with their projects (never inside this skill). Start from
`assets/filaments.blank.json` or `scripts/registry.py --registry filaments.json init --printer ...`.

## Schema

```json
{
 "schema": "gnoshcodes filament registry v1",
 "updated": "YYYY-MM-DD",
 "printers": {"P1S": {"machine_preset": "Bambu Lab P1S 0.4 nozzle", "process_preset": "0.20mm Standard @BBL X1C",
                      "plate": "Textured PEI Plate", "nozzle": "0.4"}},
 "filaments": [{
   "code": "PLA-NAVY-01",                       // MATERIAL-COLOR-NN, never reused; a new spool line gets -02
   "brand": "...", "product_line": "...", "material": "PLA",
   "color": {"name": "Navy", "hex": "#1B2A4A", "hex_is_estimate": true},
   "diameter_mm": 1.75, "inventory": "...", "drying": "...",
   "presets": {"P1S": "Bambu PLA Basic @BBL P1S 0.4 nozzle"},   // exact system preset (list_presets.py)
   "tuned": {"P1S": {
       "status": "untested | baseline | tuned",
       "filament": {"filament_retraction_length": {"value": "1.0", "tests": ["T-20261127-P1S-02"], "why": "..."}},
       "process":  {"xy_hole_compensation": {"value": "0.05", "tests": ["T-20261127-P1S-02"], "why": "..."}}}},
   "tests": [{"id": "T-20261127-P1S-02", "date": "2026-11-27", "printer": "P1S", "tile": "tuning tile v1",
              "settings": "0.20mm Standard @BBL X1C + Bambu PLA Basic @BBL P1S 0.4 nozzle + overrides",
              "changed": {"filament": {...}, "process": {...}}, "photos": "4 photos",
              "features": {"fit": "0.20", "stringing": "good", "overhang": "55", "bridge": "20"},
              "verdict": "...", "note": "..."}]
 }]
}
```

- **filament** block = settings that live in a filament preset: `nozzle_temperature`,
  `nozzle_temperature_initial_layer`, plate temperatures (`cool_plate_temp`,
  `cool_plate_temp_initial_layer`, `eng_plate_temp`, `eng_plate_temp_initial_layer`, `hot_plate_temp`,
  `hot_plate_temp_initial_layer`, `textured_plate_temp`, `textured_plate_temp_initial_layer`,
  `supertack_plate_temp`, `supertack_plate_temp_initial_layer`),
  `filament_flow_ratio`, `enable_pressure_advance` / `pressure_advance`, `filament_retraction_length`,
  `filament_retraction_speed`, `filament_wipe`, `fan_min_speed`, `fan_max_speed`, `overhang_fan_speed`,
  `overhang_fan_threshold`, `slow_down_layer_time`, `filament_max_volumetric_speed`.
- **process** block = process settings tuned for this filament: `xy_hole_compensation`,
  `xy_contour_compensation`, `elefant_foot_compensation`, `bridge_flow`, `bridge_speed`, `thick_bridges`,
  `enable_overhang_speed`, `overhang_1_4_speed`, `overhang_2_4_speed`, `overhang_3_4_speed`,
  `overhang_4_4_speed`, `overhang_totally_speed`,
  `seam_position`, `seam_gap`, `seam_slope_type`, `top_shell_layers`, `top_surface_pattern`, `ironing_type`,
  `ironing_flow`, `wall_generator`, `detect_thin_wall`, `min_feature_size`, `outer_wall_speed`,
  `outer_wall_line_width`, `outer_wall_acceleration`, `small_perimeter_speed`, `initial_layer_speed`,
  `resolution`, `enable_arc_fitting`.
- **These two lists are the only keys `registry.py` accepts**, each only in its own block. `post_process` and
  every `*_gcode` key are always refused: they run a program on the computer or commands on the printer, and a
  registry may come from someone else.
- **One closed schema.** `registry.py` checks the whole file against one schema (`SCHEMA` in the script) before
  it reads, prints or writes anything, and every write is checked again: every key, type, text length, list size,
  name pattern, range and choice. One problem anywhere refuses the whole file. Texts (verdicts, notes, brand)
  are at most 64-500 characters with no control characters; printer names are 1-32 letters, digits, `. _ -`;
  preset and plate names are 1-100 letters, digits, spaces and `. _ @ ( ) + / -`, starting with a letter or digit
  (nothing a shell treats specially inside quotes); a file is at most 2 MB. Characters are judged by the running
  Python's Unicode tables, so a character newer than them (a recent emoji on Python 3.8) counts as unassigned and
  is refused: a note written on a newer Python can be refused on an older one (it fails safe).
- **Values.** A value is a JSON number or a string (`"215"`, `"0.05"`, `"10%"`), within the range below. `int`
  is a whole number, `num` any number (up to 5 decimals), `pct` a percent written with `%`, `bool` is 0 or 1
  (not true/false), `nil` means "use the printer's value". Ranges are what a Bambu printer can do, kept
  conservative on purpose: a value outside them can still be typed into Bambu Studio by hand. Types, choices and
  which keys are percents follow Bambu Studio's `PrintConfig.cpp`.
- **Per-printer limits.** The plate and acceleration ranges below are the X1's. A value stored for a printer
  (in `tuned` or a test's `changed`) must also be within that printer's limits, told by its `machine_preset`:
  A1 mini 80 C / 10000 mm/s2, A1 100 C / 10000, P1S and P1P 100 C / 20000, X1, X1 Carbon, X1E and H2D
  110 C / 20000. Any other machine preset (a renamed user preset included) gets the lowest, 80 C / 10000.
  The registry does not rely on Bambu Studio or the firmware to clamp a higher value.

| key | block | value |
|---|---|---|
| `cool_plate_temp` | filament | int 0..110 |
| `cool_plate_temp_initial_layer` | filament | int 0..110 |
| `enable_pressure_advance` | filament | bool 0/1 |
| `eng_plate_temp` | filament | int 0..110 |
| `eng_plate_temp_initial_layer` | filament | int 0..110 |
| `fan_max_speed` | filament | int 10..100 |
| `fan_min_speed` | filament | int 0..100 |
| `filament_flow_ratio` | filament | num 0.8..1.2 |
| `filament_max_volumetric_speed` | filament | num 1..40 |
| `filament_retraction_length` | filament | num 0..5 or nil |
| `filament_retraction_speed` | filament | num 5..100 or nil |
| `filament_wipe` | filament | bool 0/1 or nil |
| `hot_plate_temp` | filament | int 0..110 |
| `hot_plate_temp_initial_layer` | filament | int 0..110 |
| `nozzle_temperature` | filament | int 165..300 |
| `nozzle_temperature_initial_layer` | filament | int 165..300 |
| `overhang_fan_speed` | filament | int 10..100 |
| `overhang_fan_threshold` | filament | one of 0%, 10%, 25%, 50%, 75%, 95% |
| `pressure_advance` | filament | num 0..0.2 |
| `slow_down_layer_time` | filament | int 0..30 |
| `supertack_plate_temp` | filament | int 0..110 |
| `supertack_plate_temp_initial_layer` | filament | int 0..110 |
| `textured_plate_temp` | filament | int 0..110 |
| `textured_plate_temp_initial_layer` | filament | int 0..110 |
| `bridge_flow` | process | num 0.5..1.5 |
| `bridge_speed` | process | num 5..150 |
| `detect_thin_wall` | process | bool 0/1 |
| `elefant_foot_compensation` | process | num 0..0.5 |
| `enable_arc_fitting` | process | bool 0/1 |
| `enable_overhang_speed` | process | bool 0/1 |
| `initial_layer_speed` | process | num 10..300 |
| `ironing_flow` | process | pct 0..100% |
| `ironing_type` | process | one of no ironing, top, topmost, solid |
| `min_feature_size` | process | pct 5..100% |
| `outer_wall_acceleration` | process | num 500..20000 |
| `outer_wall_line_width` | process | num 0.1..1.2 |
| `outer_wall_speed` | process | num 10..500 |
| `overhang_1_4_speed` | process | num 0..300 |
| `overhang_2_4_speed` | process | num 0..300 |
| `overhang_3_4_speed` | process | num 0..300 |
| `overhang_4_4_speed` | process | num 0..300 |
| `overhang_totally_speed` | process | num 0..300 |
| `resolution` | process | num 0.001..0.1 |
| `seam_gap` | process | pct 0..50% |
| `seam_position` | process | one of nearest, aligned, back, random |
| `seam_slope_type` | process | one of none, external, all |
| `small_perimeter_speed` | process | num 10..500 or pct 10..100% |
| `thick_bridges` | process | bool 0/1 |
| `top_shell_layers` | process | int 0..20 |
| `top_surface_pattern` | process | one of concentric, zig-zag, monotonic, monotonicline, globalmonotonicline, alignedrectilinear, hilbertcurve, archimedeanchords, octagramspiral |
| `wall_generator` | process | one of classic, arachne |
| `xy_contour_compensation` | process | num -0.5..0.5 |
| `xy_hole_compensation` | process | num -0.5..0.5 |

- A value is only written with the test that justifies it; `validate` fails on a value with no test.
- **status**: `untested` nothing printed; `baseline` a tile was printed and read at system settings; `tuned` at
  least one override confirmed by a reprint.

## Using it in project files

```
registry.py spec PLA-NAVY-01 --printer P1S > spec.json         # {"plate": {...overrides, "filament_color"}}
tune_project.py job.3mf job_PLA-NAVY-01.3mf --spec spec.json    # bambu-studio-settings skill
registry.py spec PLA-NAVY-01 --printer P1S --for preset > fil.json
make_preset.py --user-dir ... --type filament --base "<presets.P1S>" --name "PLA-NAVY-01 P1S" --overrides fil.json
```

Project files: nothing syncs, right for one job. A user preset syncs to the maker's Bambu account and shows on
every machine they sign in to - make one only when they ask, and tell them to restart Bambu Studio.
