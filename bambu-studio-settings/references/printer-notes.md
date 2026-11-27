# Printer notes (Bambu Lab)

Names below were read from a Bambu Studio 2.9 install (profiles 02.09.00.89). They change between versions -
always confirm with `scripts/list_presets.py` on the user's install.

## Preset naming

Machine presets: `Bambu Lab <model> <nozzle> nozzle`, e.g. `Bambu Lab P1S 0.4 nozzle`, `Bambu Lab H2D 0.4 nozzle`.
Process and filament presets end in `@BBL <code>`; non-0.4 nozzles add ` <d> nozzle`
(`0.30mm Standard @BBL H2D 0.6 nozzle`). The 0.4 nozzle has no suffix.

| Printer | Process presets (0.4) | Filament example (0.4) | Notes |
|---|---|---|---|
| X1 Carbon, X1, X1E, **P1S** | `@BBL X1C` (e.g. `0.20mm Standard @BBL X1C`, `0.16mm Optimal @BBL X1C`, `0.16mm High Quality @BBL X1C`, `0.20mm Strength @BBL X1C`) | `Bambu PLA Basic @BBL X1C`; newer installs also have `... @BBL P1S 0.4 nozzle` | A P1S has no `@BBL P1S` process presets - it uses X1C ones. Bed 256 x 256. |
| P1P | `@BBL P1P` | | |
| A1 | `@BBL A1` | `Bambu PLA Basic @BBL A1` | Bed 256 x 256, bed-slinger: tall thin parts wobble - brim, slower |
| A1 mini | `@BBL A1M` | `Bambu PLA Basic @BBL A1M` | Bed 180 x 180 |
| H2D | `@BBL H2D`: `0.08mm Extra Fine`, `0.12mm Fine`, `0.16mm Standard`, `0.16mm Balanced Quality`, `0.20mm Standard`, `0.20mm Balanced Strength`, `0.24mm Standard` | `Bambu PLA Basic @BBL H2D` | No "Optimal" or "High Quality" names. Two nozzles. |
| H2D Pro | `@BBL H2DP` | `... @BBL H2DP` | As H2D |
| H2S, H2C, P2S, A2L, H3C... | `@BBL <code>` | | Check with `list_presets.py --printers` |

Default process per printer is the machine preset's `default_print_profile` (shown by `list_presets.py`).

## Beds

From the machine presets: `printable_area` (corner points, mm), `printable_height`, `bed_exclude_area`
(e.g. an 18 x 28 mm corner at x 0, y 0 on X1/P1 printers), and on two-nozzle printers `extruder_printable_area`.

## H2D: two nozzles and reach (general notes)

- Two nozzles, left and right. `extruder_printable_area` in the 2.9 profiles: nozzle 1
  `0..325 x 0..320`, nozzle 2 `25..350 x 0..320` on a 350 x 320 bed. So the leftmost 25 mm is reachable only by
  one nozzle and the rightmost 25 mm only by the other. **An object that uses filaments on both nozzles must sit
  in x 25..325** (the area both reach); the prime tower too.
- `filament_map` gives each filament's nozzle (1-based, one entry per filament); it is only binding when
  `filament_map_mode` is `Manual`. In `Auto For Flush` / `Auto For Match` the slicer assigns nozzles at slice
  time - keep multi-color objects in the shared area to be safe. **1 = left nozzle, 2 = right nozzle**: that is
  what the app's manual grouping dialog writes (Bambu Studio source, `src/slic3r/GUI/FilamentMapPanel.cpp`,
  `FilamentMapManualPanel::GetFilamentMaps`, checked Nov 2026).
- **H2C and other one-extruder, several-nozzle printers are different:** `filament_map` stays all 1 there and the
  grouping lives in `filament_nozzle_map`. Do not apply the H2D left/right rule to them.
- Each nozzle is fed from its own AMS / external spool path; which AMS units feed which nozzle is set on the
  printer and in the app's device page. Filament slots in a project are just numbers until mapped to trays at
  print time - put the colors (`filament_colour`) and materials in the project so the mapping dialog is obvious.
- The nozzle type (Standard / High Flow / TPU High Flow) is an "extruder variant": process lists such as speeds
  have one entry per variant (`print_extruder_variant`). Set scalars and let the scripts repeat them.
- Multi-color H2D plates are the case where the CLI crashes most - verify structurally, slice once in the app.

## X1C / P1S

- One nozzle; AMS for up to 4 filaments per unit. Keep objects out of `bed_exclude_area`.
- Projects made for an X1C open on a P1S and vice versa (same process presets); the app may ask to switch
  printer preset - the embedded settings still apply.

## A1 / A1 mini

- Bed slinger (bed moves in Y). AMS lite. Tall parts and large flat parts benefit from brim / slower outer walls.
