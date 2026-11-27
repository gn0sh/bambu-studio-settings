# Rules: measurement -> setting -> why

Every setting `recommend.py` proposes carries a rule id from this table, so a reviewer can see where it came
from. Source column:

- **tested here** - a fit or print that was made and passed, recorded in your `tested.json`. Always wins.
- **Bambu default** - what Bambu Studio's own system presets do (checked against the Bambu Studio 2.x system presets: 2 walls, 15% infill, tree(auto) support at 30 deg, overhang slow-down on, classic wall generator).
- **common practice** - widely used FDM practice. A sensible start, not a law. Change the threshold when your
  own prints say otherwise, and record why.
- **geometry** - follows directly from the measured shape (for example "nothing needs support").

Thresholds assume a 0.4 mm nozzle and roughly 0.42-0.45 mm lines. For other nozzles, scale the line-based
numbers.

## Contents
- Order of precedence
- Rule table
- What is measured, and how exactly
- Limitations

## Order of precedence

1. **K1 tested settings** (`tested.json`, regex on object name). Tested keys are used as they are. On a tested
   object, geometry rules don't touch any dimensional setting (layer height, walls, wall generator, thin-wall
   detection, elephant's foot) or its brim: changing one would make it an untested part. `tested.json` holds
   print settings only: `post_process` and G-code keys are refused.
2. **L5 prime-tower plate**: with the prime tower on and 2 or more filaments on a plate, every object on that plate gets
   one layer height. Bambu Studio refuses to slice mixed heights there. A tested height on the plate wins.
   Otherwise the majority wins. An object that wanted something else gets a note: move it to its own plate.
3. Geometry and hint rules below.
4. Plate versus object: for each key, a value becomes the plate value only when more than half of the untested
   objects want it (a tie stays per object). Objects that want another value get their own rule. Every object
   that did not ask for the new plate value, tested or not (and objects the analyzer could not measure), gets a
   **P1** rule keeping the value it has now, so a plate change never reaches an object that did not ask for it.
   When the project does not set that key at all, there is no value to keep, so the key stays per object.
   Nothing already set in the project is repeated (use `--full` for a portable spec).

## Rule table

| id | measurement / input | setting | why | source |
|---|---|---|---|---|
| K1 | object matches `tested.json` | its tested keys (`layer_height`, `wall_loops`, ...) | a fit is only proven at the settings it was printed at | tested here |
| L0 | no show faces, no fit features | `layer_height` 0.20 | Bambu "Standard" height; quickest of the everyday heights | Bambu default |
| L1 | fit-relevant (load functional/structural, `fit: true`, or round holes/pins < 6.5 mm) | the height that lands exactly on the most flat z features (ties: nearest 0.20) | step tops, ledges and catches land on layer boundaries; off-grid features move by up to half a layer | common practice |
| L2 | show faces marked, or load decorative | `layer_height` 0.16 | smoother walls and slopes; Bambu's 0.16 mm presets ("Optimal" on X1C/P1S, "Balanced Quality" on H2D). The report says which z features move at 0.16 | common practice |
| L3 | show + fine detail (5+ islands under 4 mm2 in a layer, or one under 1 mm2) | `layer_height` 0.12 | small features and text stay crisp | common practice |
| L4 | top height is not on a layer boundary | note only | the top rounds up to the next layer boundary (175.0 -> 175.08 with a 0.20 first layer and 0.16 layers); `precise_z_height` can fix it | geometry |
| L5 | prime tower on + 2 or more filaments on a plate | one `layer_height` per plate | Bambu Studio cannot slice mixed heights with a prime tower | Bambu default |
| W1 | hidden / functional | `wall_loops` 2 | Bambu's default for a 0.4 mm nozzle | Bambu default |
| W2 | show faces | `wall_loops` 3 | hides infill shadowing, smoother outer wall | common practice |
| W3 | load structural | `wall_loops` 4 | most strength is in the perimeters | common practice |
| W4 | thinnest wall < 2 lines in more than 0.5% of section area | note | only 1-2 lines fit there whatever `wall_loops` says | geometry |
| W5 | > 0.3% of section area under one line, or fine detail | `wall_generator` arachne | Arachne varies line width rather than dropping thin features | common practice |
| T1 | top (+z) shows and `top_shell_layers` < 5 | `top_shell_layers` 5 | stops pillowing over sparse infill | common practice |
| T2 | load structural | top 5 / bottom 4 or more | solid skins carry load | common practice |
| O1 | show + more than 20 mm2 of overhang at 45-72 deg from vertical | `outer_wall_speed` x0.75 | cleaner overhang edges on faces people see | common practice |
| O2 | overhangs and `enable_overhang_speed` off | `enable_overhang_speed` 1 | Bambu slows down on overhangs by default | Bambu default |
| O3 | more than 5 mm2 steep overhang | note: cooling | overhang fan is a filament setting, not per object | common practice |
| S1 | flat area with no support at either end > 2 mm2, or > 25 mm2 at 60-72 deg, or a bridge > 50 mm | `enable_support` 1 (or 0 when nothing needs it) | Bambu's threshold 30 deg from horizontal supports the >60-from-vertical band | geometry |
| S2 | support needed | `support_type` tree(auto) for show parts or < 400 mm2 unsupported, else normal(auto) | tree marks less and removes easily; normal gives flatter large ceilings | common practice |
| S3 | every unsupported area has only the bed below | `support_on_build_plate_only` 1 | no support scars on the part itself | geometry |
| S4 | bridge span > 20 mm | note | check sag; `thick_bridges` 1 is stronger, 0 looks cleaner | common practice |
| B1 | height / base width > 3 and height > 15 mm (base width: short side of the smallest rectangle, at any angle, around the first-layer contact) | `brim_type` outer_only, `brim_width` 5 (not on tested parts: note) | tall narrow parts rock loose | common practice |
| B2 | contact < 100 mm2 and height > 10 mm | `brim_type` outer_only | small footprint, little grip | common practice |
| B3 | bottom (-z) shows | no brim (note) | a brim scars the bottom edge | common practice |
| B4 | contact > 20000 mm2 | note | corners of big flat parts lift | common practice |
| BP0 | `--plate` not given | note: ask which plate (or confirm the project's `curr_bed_type`) | the plate decides the bed temperature; never assume Textured PEI | common practice |
| BP1 | `--plate` differs from the project's `curr_bed_type` | `curr_bed_type` on the plate | Bambu Studio uses the bed temperature of the plate `curr_bed_type` names | Bambu default |
| BP2 | smooth plate (Cool, Engineering, High Temp, Supertack) | note | glossy bottom; less grip on corners and small footprints than Textured PEI; first layer reads differently | common practice |
| BP3 | Textured PEI | note | textured bottom; Bambu's start G-code lowers Z 0.02 mm (0.01 on a 0.2 nozzle), so first-layer squish and elephant's foot read differently than on smooth plates | Bambu default |
| BP4 | bottom (-z) shows | note: the finish that plate gives | textured on Textured PEI, glossy on smooth plates | common practice |
| BP5 | PETG (`filament_type` contains PET) on a smooth plate | note: release agent | PETG can fuse to smooth PEI or glass and tear it | common practice |
| BP6 | smooth plate + contact > 20000 mm2, bottom not shown, brim free | `brim_type` brim_ears | big flat parts lift at the corners more on smooth plates | common practice |
| B5 | round holes/pins starting at the bed | note: keep `elefant_foot_compensation` | the squashed first layer closes holes / fattens pins | common practice |
| F1 | round holes/pins under 6.5 mm | note | holes print undersize, pins oversize (~0.1-0.2 mm); tolerances are tied to tested settings | common practice |
| M1 | show sides marked, +Y (back) not among them | `seam_position` back | the seam line goes where nobody looks | common practice |
| M2 | +Y shows too | `seam_position` aligned (and rotate the part if you can) | one straight line instead of scattered blobs | common practice |
| M3 | show sides + curved walls > 30% of wall area | `seam_slope_type` external | scarf seam hides the seam on smooth round walls | common practice |
| I1 | decorative and infill > 15% | 10% | nothing to carry | common practice |
| I2 | functional and infill < 15% | 15% | Bambu default | Bambu default |
| I3 | structural | 30% gyroid | strong in every direction | common practice |
| N1 | 20%+ of sampled layers under 25 mm2 | note: minimum layer time | the slicer slows down (filament `slow_down_layer_time`); print with other parts for cooling time | common practice |
| N2 | fine detail | note | text, pins and tips need cooling and maybe finer layers | common practice |
| P1 | the plate value changes and this object did not ask for it | a per-object rule at its current value | an object only gets changes it asked for; tested parts stay tested | project value kept |

Bambu-specific keys used: `layer_height`, `wall_loops`, `wall_generator`, `top_shell_layers`,
`bottom_shell_layers`, `outer_wall_speed`, `enable_overhang_speed`, `enable_support`, `support_type`,
`support_on_build_plate_only`, `brim_type`, `brim_width`, `seam_position`, `seam_slope_type`,
`sparse_infill_density`, `sparse_infill_pattern`. All are object-level keys, so they work per object in a project,
except `curr_bed_type` (BP1), which is project-wide only and only ever goes in the spec's `plate`.

## What is measured, and how exactly

| measurement | method | exact? |
|---|---|---|
| placement | build item x component transforms; z measured from the object's lowest point | exact |
| footprint, height | mesh bounding box | exact |
| area facing each direction, curved-wall share | per-triangle normals and areas | exact (curved share is a heuristic: wall area in azimuth bins under 3% each) |
| overhang bands | per-triangle normals and areas; faces steeper than 60 deg from vertical whose center has solid 0.03 mm below on the grid (a part resting on another) are left out | exact per triangle, except that leaving out resting faces is decided per triangle center, on the grid (+/- one cell): a large face only partly resting on another part is kept or left out whole |
| internal faces (a part resting on another) | down faces whose center has solid 0.03 mm below are left out | approximate |
| flat z features, layer-height fit | flat triangles (|n_z| > 0.999); heights sorted, a height within 0.006 mm of a level's lowest joins it; boundaries at first layer + k x layer height | exact |
| islands, round holes/pins | polygon cross-sections of the mesh (z slices; x and y slices for horizontal holes), circularity > 0.88 | exact sections at sampled heights. A hole and pin of the same size at the same place = two parts meeting (inlay), not counted |
| first-layer contact, thin walls, small layers, volume | vertical rays on a grid (auto 0.1-0.4 mm, `--res`; made coarser, with a note, when the grid would pass 8 million cells or one object 12 million ray crossings or 100 million ray/triangle tests) | +/- one grid cell |
| base width (B1) | short side of the smallest rectangle, at any angle, around the first-layer contact cells | +/- one grid cell |
| wall thickness | g = the shortest run through a cell in 4 directions (x, y, both diagonals); a cell's thickness is the largest g at the middle of one of its own runs that is at most 1.5 x that g (a wall's cross run is centred on the wall; a square's or a disc's corners and rim have a run through its middle), then the largest value among its 3x3 neighbours (removes staircase artefacts); thinnest = the value reached by at least 0.5 mm2 of a layer; when no level has that much section (a post under about 0.7 x 0.7 mm), the smallest value measured, with `thinnest_approximate` true | approximate, measured at about 50 even levels plus just above flat features (up to 40). A square section reads its width, a round one about 88-95% of its diameter; the joins of walls read up to ~40% thicker, and the tip of a tapering wall reads a little thicker than it is |
| bridges and spans | flat down faces above the bed (> 72 deg from vertical), cells within one line of the layer below removed (those print as ordinary overhang); span = shortest x or y run with solid below at both ends | approximate; diagonal spans not measured |

## Limitations

- Supports: the analysis flags flat ceilings with no support and steep overhangs. It does not model the slicer's
  own overhang logic. Cross-check with `slice_check.py --json` (pass it to `recommend.py --slice-json`) and look
  at the slice preview when the two disagree.
- Wall thickness is a grid measure: +/- one cell, and the 4-direction runs overestimate walls at odd angles by
  up to ~8%. Use `--res 0.05` for a closer look at a small part (slower).
- Bridge spans are measured along x and y only. A diagonal bridge reads longer than it is.
- Horizontal holes are found from x/y slices at about 60 positions, so very short ones can be missed. Non-round
  tolerance features (slots, dovetails, snap hooks) are not detected; mark the part `fit: true` instead.
- "Show faces" come from you (`--show` / hints). The analyzer cannot know what is seen in use.
- Variable layer height ranges and modifier parts in a project are not analyzed (modifiers are skipped).
- Limits, so a hostile or broken file cannot exhaust memory or time: a .3mf is read only through the
  bambu-studio-settings reader (zip, size and XML limits, no DTDs; component cycles, depth over 16, more than
  100000 component uses or 10 million vertices are refused); at most 4 million triangles in all (`--max-triangles`);
  STL files up to 512 MB; an object over 1 m, or with coordinates that are not finite, is reported as not measured;
  at most 400 flat-ceiling heights are measured per object (the largest), and at most 100 cross-sections are added
  above flat features; past 12 million ray crossings or 100 million ray/triangle tests the grid is made coarser
  (up to 2 mm), and one run casts at most 100 million crossings and 400 million tests in all.
- Rules are a starting point. When a print proves a rule wrong for your printer, record the result in
  `tested.json` (it wins) and adjust the threshold here with a dated note.
