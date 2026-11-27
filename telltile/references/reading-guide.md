# Tuning tile v1 - photo reading guide (for the AI agent)

You are reading phone photos of a printed **gnoshcodes tuning tile v1** and recommending Bambu Studio setting
changes. This guide says, feature by feature, what good and bad look like in a photo, the likely cause, and the
setting to change with a starting step. Setting names are given as the Bambu Studio label and the key
(`key_name`) that presets and project files use.

## The rules

1. **Change one thing at a time.** Pick the single most important fault, change one setting by one step, reprint
   the tile, compare. Two changes at once and you cannot tell which one worked.
2. **Read the ID panel first.** It says the printer, layer height and tile version (generic tiles: nozzle 0.4,
   layer 0.20, version, and the filament written on the blank strip by hand). If the photo does not show it, ask.
   A verdict without knowing printer + filament + settings cannot be recorded. **Ask which build plate** it was
   printed on (Cool, Engineering, High Temp / smooth PEI, Textured PEI, Supertack) unless the maker's registry
   already names it for that printer; never assume Textured PEI. Results belong to printer + plate + filament.
3. **Fix in this order** (earlier faults cause later ones): wet filament / dirty plate -> first layer and elephant
   foot -> flow ratio (dimensions, top surface) -> temperature -> cooling (overhangs, bridges, tip) -> retraction
   (stringing) -> fit compensation (pins, holes) -> seam -> cosmetic (ironing, small text).
4. **Record every print**: date, printer + nozzle, filament (code, brand, preset), process preset, every override,
   the photos, a verdict per feature. Use `scripts/registry.py record --from <file>` on the maker's own
   `filaments.json`, the fields in a JSON file you wrote (SKILL.md rule 8: never on a command line). A value is
   "tuned" only after a reprint confirms it.
5. **Say how sure you are.** A phone photo shows texture, gaps and droop well; it does not measure tenths of a
   millimeter. For dimensions, ask for caliper readings. When unsure, ask for the one extra photo that settles it.
6. **Where a setting goes:** filament-level settings (temperatures, flow ratio, pressure advance, filament
   retraction, fan, slow-down) go in a **filament user preset** or the project's filament settings, never in the
   process. Process-level settings tuned for this filament (x-y compensation, elephant foot compensation, seam,
   top layers, ironing, overhang/bridge speeds) go in the **project** or a process user preset named after the
   filament. Use the `bambu-studio-settings` skill (`make_preset.py`, `tune_project.py`) to write them, and the
   registry's `spec` command to produce the JSON those scripts take.

## Photos to ask for

Send `references/photo-instructions.md` as is (the one copy of the photo list).

## Tile map

Front edge at the bottom of a top photo. Size 112 x 84 mm, printed flat, no supports, 0.20 mm layers.

| Zone | Where | Features and labels |
|---|---|---|
| Overhang fins | front edge | 7 fins, undersides at 30, 40, 45, 50, 55, 60, 70 degrees **from vertical** (label on each fin top). 70 deg is the hardest. |
| ID panel | front-left, behind the fins | engraved: `printer | layer | version` and filament code (or a blank strip on the generic tile) |
| Thin ribs / slots | front-right | ribs 0.4, 0.6, 0.8, 1.0, 1.2, 1.6 mm wide (labels in front); slot gauge in front of them with slots of the same widths |
| 20 mm boss | middle-left | 20.00 x 20.00 mm square, 6 mm tall, standing on the bed inside a window (so its bottom edge shows elephant foot); its top is the flat-top-surface sample |
| Fit pins | middle | 6 pins, 5 mm, printed in place inside collars with radial clearance 0.10, 0.15, 0.20, 0.25, 0.30, 0.40 mm (label in front of each) |
| Hole pad | middle-right | holes 10, 3, 4, 5, 6 mm (labelled) |
| Wordmark samples | back-left | "gnoshcodes" at ascender height 3.5, 3, 2.5 mm: raised (left column) and engraved in a pad (right column) |
| Seam / tip / strings | back-middle | seam cylinder 10 mm (SEAM), cone tip (TIP), two 4 mm towers 22 mm apart (STR) |
| Bridges | back edge | spans 5, 10, 15, 20, 25 mm (label on each deck) |
| Brand column | right edge | gnoshcodes hook mark at 10, 6 and 3 mm tall (labelled); dot pins 2, 1.5, 1 mm diameter (labelled) |

Reference renders of a perfect tile: `assets/renders/tile-top.png`, `tile-three-quarter.png`, `tile-closeup.png`.

---

## Feature by feature

Steps are starting steps: one step per reprint, then halve the step when you are close.

### 1. First layer and elephant foot (photo 1, 3, calipers)
- **Good:** bottom edge of the tile and of the 20 mm boss straight, no lip; first-layer lines on the bed side fused,
  no gaps; boss measures the same at the bottom edge and 3 mm up.
- **Elephant foot:** a flared lip on the first 1-2 layers, boss wider at the bottom (e.g. 20.3 vs 20.0), pins fused
  at the base. -> **Elephant foot compensation** `elefant_foot_compensation` +0.05 mm (default 0.15); if the plate
  is hot for the material, **initial layer bed temperature** (`textured_plate_temp_initial_layer` or the key for the
  plate in use) -5 C.
- **Gaps between first-layer lines / corners lifting:** clean the plate (dish soap, water), check the plate type in
  the project matches the plate on the printer (`curr_bed_type`), then initial layer bed temperature +5 C,
  **initial layer speed** `initial_layer_speed` -10 mm/s. (Z offset is the printer's own calibration, not a slicer
  setting; run the printer's bed levelling / calibration if the first layer is uneven across the plate.)
- **Textured vs smooth plate - first-layer gauges read differently.** On the Textured PEI Plate, Bambu's start
  G-code lowers Z by 0.02 mm (0.01 mm on a 0.2 mm nozzle) so the first layer fills the texture: the first layer
  looks more squished, and the boss and pin bases can show a little more elephant foot than on a smooth plate at
  the same settings. The bed side is matte and textured there (glossy on smooth plates), which hides fine line
  gaps. Judge against the plate the tile was printed on, and never carry a first-layer or elephant-foot verdict
  from one plate to another. PETG on a smooth plate can fuse to it: use a release agent (glue stick).
- **Over-squished, rough ridged first layer:** usually printer-side Z offset; in the slicer try initial layer bed
  temperature -5 C and check flow ratio (section 4).

### 2. Overhang fins 30-70 deg (photo 3, front low)
- **Good:** 30-50 deg undersides smooth and straight; 55-60 deg slightly textured; 70 deg may show some droop or a
  curled tip - normal for most PLA without supports. PETG droops earlier (good to ~50-55 deg).
- **Droop / curl / rough undersides starting at a lower angle than above** (e.g. 50 deg already ragged):
  1. cooling: **overhang fan speed** `overhang_fan_speed` to 100 % (PLA); PETG +10 %; **fan threshold**
     `overhang_fan_threshold` lower (e.g. 50% -> 25%) so the fan comes on earlier;
  2. **Slow down for overhangs** `enable_overhang_speed` = on, then **overhang speeds** `overhang_3_4_speed` /
     `overhang_4_4_speed` -20 %;
  3. **nozzle temperature** `nozzle_temperature` -5 C;
  4. as a last resort, a lower layer height (0.16) for parts with steep overhangs - note this changes every
     z-height of a part, so it is a per-project decision, not a filament setting.
- **Edges curling up on the 60-70 fins** (warped tip catching the nozzle): more cooling as above, then
  `slow_down_layer_time` +2 s.
- Record the **steepest angle that still looks clean** ("clean to 55 deg"): it tells future designs when they need
  supports or a chamfer.

### 3. Bridges 5-25 mm (photo 4, back low)
- **Good:** 5-15 mm flat and tight; 20-25 mm may sag a little (under 0.5 mm) with strands touching.
- **Sagging / loose strands:** **bridge flow** `bridge_flow` -0.05 (1.0 -> 0.95); bridge cooling = overhang fan
  (`overhang_fan_speed` 100 %); **bridge speed** `bridge_speed` -10 mm/s; nozzle temperature -5 C.
- **Strands snapped / gaps in the deck:** bridge flow +0.05, bridge speed -10; turn **thick bridges**
  `thick_bridges` on for long spans.
- Record the **longest clean span** ("clean to 20 mm").

### 4. Dimensional accuracy (calipers on the boss and the 10 mm hole; photo 1 for holes)
- **Good:** boss 20.00 +-0.10 both ways; 10 mm hole 10.00 +-0.10; small holes round and open (a 3 mm drill
  shank or a 3 mm pin slides through the 3 mm hole).
- **Boss too big AND holes too small** (everything "fat"): over-extrusion -> **flow ratio** `filament_flow_ratio`
  -0.02 (filament setting). Check the top surface (section 9) agrees: ridged, shiny tops.
- **Boss too small AND holes too big**, gaps on the top surface: under-extrusion -> flow ratio +0.02; then check
  the filament is dry and not tangled, and the **max volumetric speed** `filament_max_volumetric_speed` is not too
  high for this filament (-2 mm3/s).
- **Boss right, holes small** (normal for FDM): **X-Y hole compensation** `xy_hole_compensation` + (half the
  diameter error), e.g. hole 9.80 -> +0.10. Steps of 0.05.
- **Boss off, holes right:** **X-Y contour compensation** `xy_contour_compensation` - (half the error), e.g. boss
  20.16 -> -0.08.
- **Holes oval:** belt tension / mechanical, not a slicer setting - tell the maker.
- Only trust caliper readings for size. A photo can show "the 3 mm hole is closed" or "oval", not 0.1 mm.

### 5. Fit pins (the maker's "tightest one that moves")
- The pins are 5 mm, printed in place with radial clearance 0.10-0.40 mm. Typical Bambu PLA at 0.20 mm: 0.10
  fused, 0.15 stiff or fused, **0.20 turns**, 0.25-0.40 loose.
- **The tightest moving clearance is the design clearance for this printer + filament + settings.** Record it
  (registry test record, feature `fit=0.20`). Use it in designs; do not try to "fix" it with settings unless the
  maker needs a tighter fit.
- **Even 0.25 or 0.30 fused:** over-extrusion (flow ratio -0.02, section 4), elephant foot (pins welded only at
  the bottom: section 1), or too hot (nozzle temperature -5 C). Then **X-Y hole compensation** +0.05 and/or
  **X-Y contour compensation** -0.05 (opens every printed fit - note it changes all dimensions).
- **Pins rattle at 0.15:** fine for most uses; if a press fit is wanted, X-Y hole compensation -0.05.
- **Fit settings are specific to layer height, walls and wall order.** A fit found at 0.20 mm / 2 walls may change
  at 0.16 mm. Always record the settings with the result.

### 6. Stringing (photo 2 and 3: between the two STR towers, the cone and the cylinder)
- **Good:** no hairs between the towers; at most a few wisps that brush off.
- **Hairs / webs:** in this order
  1. **dry the filament** (PLA 50-55 C, PETG 65 C, 8 h) - wet filament strings whatever the settings, and also
     pops, bubbles and gives a rough, fuzzy surface;
  2. **nozzle temperature** `nozzle_temperature` -5 C (stay inside the filament's range);
  3. **retraction length** `filament_retraction_length` +0.2 mm (filament override; the machine default is
     0.8 on P1S/X1, 0.4-1 on H2D - read the project for the real value); stop at about 2 mm on direct drive;
  4. **retraction speed** `filament_retraction_speed` +10 mm/s (machine default 30); wipe is on by default
     (machine `wipe`; a filament can override with `filament_wipe`).
- **Blobs at the start of lines after travel** (opposite problem): retraction too long or deretraction extra -
  retraction length -0.2.
- PETG strings more than PLA; a few fine wisps are normal.

### 7. Seam (photo 2: the SEAM cylinder and the boss corners)
- **Good:** one thin seam line (default `seam_position` = aligned) or seams hidden in the boss corners; no blobs
  or gaps.
- **Blobs / zits on the seam:** **seam gap** `seam_gap` +5 %; wipe on (machine `wipe`, default on); pressure advance
  (`enable_pressure_advance` = 1 with `pressure_advance` from a Bambu flow-dynamics calibration).
- **Gap or notch at the seam:** seam gap -5 %.
- **Seam line in the wrong place for show parts:** `seam_position` = back (moves it to the back, +Y), or scarf
  seam `seam_slope_type` = external for round show faces (see the bambu-studio-settings cookbook). This is a
  per-project choice more than a filament setting.

### 8. Pointed tip / small layers (photo 2: TIP cone)
- **Good:** a sharp point, the last 1-2 mm may be slightly soft.
- **Melted, blobby or bent tip:** not enough time to cool -> **layer time** `slow_down_layer_time` +2 s, **max
  fan** `fan_max_speed` up (PLA to 100 %), nozzle temperature -5 C. PETG needs much less fan; for PETG try
  slow-down first.
- The same fault on any small or tall thin part (towers, pins tops).

### 9. Top surface (photo 1 and 2: boss top, ID panel, hole pad top)
- **Good:** flat, even lines, no holes, no ridges.
- **Pillowing / holes over infill:** **top shell layers** `top_shell_layers` +1 (default 5 at 0.20).
- **Ridged, shiny, raised lines:** over-extrusion -> flow ratio -0.02 (section 4).
- **Gaps between top lines:** under-extrusion -> flow ratio +0.02.
- **Want it glassy:** **ironing** `ironing_type` = top (or topmost) - costs time; then `ironing_flow` 10 % +-2 %.
- **Uneven sheen:** `top_surface_pattern` = monotonicline.

### 10. Thin ribs and slot gauge (photo 1 and 2)
- **Good:** all six ribs (0.4-1.6 mm) present and straight; 0.4 may print as a single line; slots 0.6 and up
  open, 0.4 slot may be closed.
- **0.4 / 0.6 ribs missing:** the slicer drops walls thinner than its minimum: **wall generator**
  `wall_generator` = arachne (the 0.20 mm Standard presets use classic), or keep classic and turn
  **detect thin wall** `detect_thin_wall` on; with arachne, **min feature size** `min_feature_size` lower by 5 %.
- **Ribs wavy or blobby:** too hot or too fast for the small layer: nozzle temperature -5 C, `outer_wall_speed`
  -20 %.
- **Slots 0.6-0.8 closed:** over-extrusion (flow ratio) or X-Y contour compensation -0.05.
- Record the **thinnest rib** and **narrowest open slot**: design rules for thin walls and gaps.

### 11. Wordmark samples - smallest legible size (photo 1, close)
"gnoshcodes" at ascender (h/d) height 3.5, 3 and 2.5 mm; left raised 0.6 mm, right engraved 0.6 mm. Thin letter
strokes at 2.5 mm are about 0.35 mm wide - near the limit of a 0.4 nozzle on purpose.
- **Good:** raised 3.5 and 3 readable letter by letter; 2.5 readable as a word. Engraved 3.5 and 3 open; 2.5 may
  partly close.
- **Raised letters blobby / joined:** nozzle temperature -5 C, **outer wall speed** `outer_wall_speed` -20 %,
  flow ratio -0.02.
- **Raised letters missing strokes** (the slicer drops strokes thinner than its minimum; compare with the G-code
  preview): `min_feature_size` lower, `wall_generator` = arachne.
- **Engraved letters filled in:** over-extrusion (flow ratio -0.02), **X-Y hole compensation** +0.05
  (engravings count as holes), or `outer_wall_line_width` -0.02.
- Record the **smallest raised and smallest engraved size that reads**: the minimum text size for future labels.

### 12. Hook mark set - curve smoothness and small detail (brand column, photo 1 close, photo 2)
The gnoshcodes hook mark, raised 0.6 mm, at 10, 6 and 3 mm tall.
- **Good:** 10 mm hook: smooth even curve, no flat facets, no ripples; the dot round and separate from the hook.
  6 mm: same, slightly softer. 3 mm: the hook and dot still read as two shapes.
- **Facets / flat spots on the curve:** **resolution** `resolution` lower (0.012 -> 0.008), **arc fitting**
  `enable_arc_fitting` on (on in the X1C/P1S preset, off in the H2D one).
- **Ripples / wobble along the curve (ringing):** **outer wall speed** `outer_wall_speed` -20 % (default 200 mm/s on a standard nozzle), outer wall
  acceleration `outer_wall_acceleration` -20 %; a mechanical cause (belts, loose plate) if it persists.
- **3 mm hook: dot merged into the hook:** over-extrusion (flow ratio -0.02) or too hot (-5 C).
- **Small curves bulging at the start/end point:** pressure advance (section 7), **small perimeter speed**
  `small_perimeter_speed` lower (default 50 % of outer wall speed -> 40 %).

### 13. Dot pins - small circles (brand column, photo 2)
Round pins 2, 1.5 and 1 mm across, 1.2 mm tall, the hook's dot as a cylinder.
- **Good:** 2 and 1.5 round and upright; 1 mm present, may be slightly soft on top.
- **Missing or melted 1 mm pin / blob on top:** cooling (`slow_down_layer_time` +2 s, `fan_max_speed` up), nozzle
  temperature -5 C.
- **Oval or lumpy small circles:** `small_perimeter_speed` 50 % -> 40 %, pressure advance (section 7).
- Calipers: 2 mm pin should read 2.0 +-0.1; consistently over -> X-Y contour compensation (section 4).

### 14. Gnoshcodes brand as a fine-detail test (all brand items together)
The wordmark samples, hook set and dot pins are the tile's fine-detail tests: if the brand reads cleanly at every
size down to 3 mm / 2.5 mm, small features, text and curves will print cleanly in real parts at these settings.
When they don't, use sections 11-13; when they do, say so in the record ("fine detail: all brand items clean").

---

## Writing the result

After each tile, give the maker:
1. One verdict line per zone (good / slightly off / bad + what you saw), with which photo shows it.
2. **One** recommended change: setting label, key, old value -> new value, and whether it belongs in the filament
   preset or the project / process.
3. What to look at on the next tile to confirm it.
4. The record, written to a file with your file tool, e.g. `{"code": "PLA-NAVY-01", "printer": "P1S", "verdict":
   "strings between towers; rest good", "feature": {"stringing": "bad", "fit": "0.20", "overhang": "55", "bridge":
   "20"}}`, and after a confirming reprint `{"code": "PLA-NAVY-01", "printer": "P1S", "verdict": "stringing gone",
   "set_filament": {"filament_retraction_length": "1.0"}, "status": "tuned"}`; then
   `registry.py record --from record.json --dry-run`, and without `--dry-run` once the maker says yes.
5. When a filament is tuned: `registry.py spec <code> --printer <printer>` gives the JSON for `tune_project.py`
   (projects) and `--for preset` for `make_preset.py` (a filament user preset; it syncs to the maker's Bambu
   account - ask first).
