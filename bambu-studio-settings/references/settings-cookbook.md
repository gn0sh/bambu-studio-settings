# Settings cookbook

Key names are the ones Bambu Studio writes in presets and project files. Values are strings. Keys that hold one
value per extruder / extruder variant are lists; the scripts repeat a scalar for every entry. When unsure a key
exists, check `assets/known_setting_keys.txt` or a project saved by the user's own Studio version.

## Contents
- Plate-wide or per object?
- Seam
- Scarf seam
- Walls and top layers for show faces
- Layer height (and why "z features in 0.2 mm steps want 0.20 mm")
- Brim
- Flush into infill / objects / support (multi-color waste)
- Supports
- Infill
- Prime tower
- Recording what a print was made at

## Plate-wide or per object?

| Put it... | When |
|---|---|
| In a user preset | The user wants the same setting on many future jobs ("all my photo prints: seam back, 3 walls"). Syncs to their account. |
| Plate-wide in the project (`project_settings.config`) | It is right for most objects in this job. |
| Per object in the project (`model_settings.config`) | One object differs: a show piece next to functional parts, a part that was tested at other settings, a part that needs its own seam or wall count. |
| Per part (inside one object) | Rare; mainly the filament slot (`extruder`) of each part of a multi-color object. Modifier parts (subtype `modifier_part`) can carry settings for a region of an object. |

Bambu Studio only honours keys that its object settings list allows (quality, strength, speed, support, flush
options, brim, seam, infill...). Machine and filament settings, `enable_prime_tower`, `print_sequence`,
`wipe_tower_x/y` and the like are plate/project-only; an object-level copy is ignored. When in doubt, slice and
check (`slice_check.py` shows each object's real layer heights), or open the object's settings in the app.

## Seam

`seam_position`: `nearest` (default-ish, hides seams in corners), `aligned` (one straight line), `back` (the seam
line goes to the back, +Y side - away from the camera in a front-on product photo), `random` (scattered blobs,
no line; good on organic shapes, bad on flat faces). For listing photos of boxy prints, `back` with the show face
toward -Y (front of the bed) is the usual choice. `seam_gap` and `seam_placement_away_from_overhangs` fine-tune it.

## Scarf seam

A sloped ("scarf") joint that hides the seam on smooth walls. Keys:
- `seam_slope_type`: `none` | `external` (outer walls) | `all` (outer and inner walls / holes)
- `seam_slope_conditional` = `1` + `scarf_angle_threshold` (degrees): only use scarf where the wall is smooth
  enough (corners still get a normal seam, which hides better there)
- `seam_slope_start_height` (e.g. `10%`), `seam_slope_steps`, `seam_slope_min_length`, `seam_slope_entire_loop`,
  `seam_slope_inner_walls`, `apply_scarf_seam_on_circles`
- Filament presets also carry `filament_scarf_seam_type` / `filament_scarf_height` / `filament_scarf_gap` /
  `filament_scarf_length`; `override_filament_scarf_seam_setting` = `1` makes the process values win.
Use on rounded show faces (cylinders, rounded boxes). On flat-faced prints, `seam_position=back` is usually enough.

## Walls and top layers for show faces

- `wall_loops` 3-4 on show pieces hides infill shadowing through thin walls and makes the outer wall smoother;
  2 is enough for hidden functional parts. Walls change wall thickness, so they can change the fit of thin
  features - a tested part keeps its tested wall count.
- `top_shell_layers` 5-6 (or `top_shell_thickness` in mm) stops "pillowing" over sparse infill on flat tops.
- `top_surface_pattern` `monotonic` / `monotonicline` for even sheen; `ironing_type` (`no ironing`, `top`,
  `topmost`, `solid`) for glassy flat tops at a time cost.
- `wall_sequence` `inner wall/outer wall` (default, better surface) vs `outer wall/inner wall` (better dimensional
  accuracy on overhang-free parts).
- `bottom_shell_layers` matters for parts photographed from below or with text on the bed side.

## Layer height

Choose from the system presets for the printer (`list_presets.py` shows each preset's layer height). Lower layers
are smoother on curves and slopes and cost time roughly in proportion. Things to know:

- **Z features in 0.2 mm steps want 0.20 mm.** Every surface that faces up or down lands on a layer boundary.
  With a 0.20 mm first layer and 0.20 mm layers, boundaries are at 0.2, 0.4, ... so a ledge designed at 4.6 mm or
  a catch 3.0 mm up prints exactly there. At 0.16 mm (first layer still 0.20) boundaries are 0.20, 0.36, 0.52 ...
  so 4.6 mm becomes 4.52 or 4.68, and every feature moves by a different amount (up to half a layer). Snap fits,
  sliding lids, stacking lips and catches designed and tested at 0.20 mm can bind or rattle at 0.16 mm even
  though 0.16 "looks better". Same logic for 0.24/0.28: check your z steps are multiples.
- The top of a part that is not a multiple of the layer height is rounded: a 14.5 mm tall tray printed at
  0.20 mm ends at 14.6 mm (seen in G-code). Fine for most parts, not for something that must fit under a lid.
- Different layer heights per object on one plate work (each object keeps its own z levels; the G-code then has
  odd small steps where they interleave - that is normal) **except** when the plate prints a prime tower with 2+
  filaments: then every object on that plate needs the same layer height. `tune_project.py` refuses that case.
- `initial_layer_print_height` is usually 0.2 regardless of layer height; keep it unless there is a reason.
- Variable layer height / "adaptive" changes are stored as layer ranges (`Metadata/layer_heights_profile.txt`,
  `layer_config_ranges.xml`) - check those exist before assuming an object is at one height.

## Brim

`brim_type`: `auto_brim`, `outer_only`, `inner_only`, `outer_and_inner`, `brim_ears`, `painted`, `no_brim`
(check the exact list on the user's version). `brim_width` (mm), `brim_object_gap` (mm; ~0.1 peels cleanly).
Use a brim on tall narrow parts, sharp corners on large flat parts, and slippery materials; turn it off
(`no_brim`) for photo pieces where a brim scar on the bottom edge would show, or when the part has a chamfered
bottom edge that already resists lifting. Brim is settable per object.

## Flush into infill / objects / support

Multi-color prints purge filament at every color change. Reuse some of it:
- `flush_into_infill` = `1`: purge into the object's sparse infill (invisible, saves material)
- `flush_into_support` = `1`: purge into support (default on)
- `flush_into_objects` = `1`: purge into a whole object - make that object a "waste" part you do not care about
Set these per object for the objects that should absorb purge. Purging into infill mixes colors inside the part,
which can show through thin walls in light colors - use enough walls. The prime tower is still needed.

## Supports

`enable_support` `1`; `support_type` `normal(auto)` / `tree(auto)` (or `(manual)` = only painted / enforcer
areas); `support_threshold_angle` (degrees from horizontal; lower = less support); `support_on_build_plate_only`;
`independent_support_layer_height`. `slice_check.py` reports grams of support from the G-code - use it to compare
orientations. Support blockers and enforcers are parts of subtype `support_blocker` / `support_enforcer`.

## Infill

`sparse_infill_density` (e.g. `15%`), `sparse_infill_pattern` (`grid`, `gyroid`, `cubic`, `honeycomb`,
`adaptivecubic`, `lightning`, ...). Gyroid is a common choice for strength-in-all-directions and translucent
filaments; lightning for fast decorative parts. Infill pattern can show through thin light-colored walls.

## Prime tower

`enable_prime_tower` `1` is needed for clean color changes; position with `wipe_tower_x` / `wipe_tower_y` (one
entry per plate) and `prime_tower_width`. Keep objects clear of it - `verify_project.py` warns when an object
box meets the tower square (the tower's depth grows with flush volume, so leave margin). On H2D the tower must
sit where both nozzles can reach it.

## Recording what a print was made at

Whenever a print is tested, write down printer + nozzle, filament, process preset name and the overrides that
matter for the result (layer height, walls, top layers, infill, seam), e.g.:

```
2026-11-20  lid v3 fit test   P1S 0.4 | PLA Basic | 0.20mm Standard @BBL X1C | 2 walls | PASS (slides, holds)
```

A later change to any of those means the fit is untested again. Keep tested objects at their tested settings in
the project with per-object overrides, and say so in the hand-over.
