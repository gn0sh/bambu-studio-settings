# Tuning tile v1 - design spec, printing and checks

## The print

- 112 x 84 mm, 10.6 mm tall, flat on the bed, one object (plus six print-in-place pins as separate shells inside
  it). Fits any bed of 120 x 90 mm or more (about 4 mm clear on each side).
- Designed for a **0.4 mm nozzle, 0.20 mm layers, 0.20 mm first layer**: every z step is a multiple of 0.2.
  Printed at other layer heights the readings shift (fits, overhang heights) - record the layer height.
- **No supports.** The slicer finds no support need with supports off (`enable_support` = 0, the default).
  The 70 deg fin is meant to be hard.
- Bambu Studio estimates (0.20mm Standard, system Basic filament presets, Textured PEI Plate), including the
  printer's start sequence: P1S PLA about 52 min / 17.7 g; P1S PETG about 56 min; H2D PLA about 56 min; H2D PETG
  about 59 min / 17.0 g. The printing itself is about 38-42 min; the rest is heating, levelling and purge.

## Features (model coordinates, mm; X right, Y back, front edge y = 0)

| Feature | Geometry | Reads |
|---|---|---|
| Base plate | 112 x 84 x 0.6 | first layer, warping |
| Overhang fins | 7 fins 9 wide at x 9.8 + 13.4 i, root y 9-12, undersides at 30/40/45/50/55/60/70 deg from vertical, 5 mm projection (heights snapped to 0.2) | overhang limit, cooling |
| ID panel | 52 x 15 x 1.4 pad, text engraved 0.6: line 1 `printer | layer | version` (generic: `0.4 | 0.20 | v1`), line 2 filament code (generic: blank strip for a marker) | which print this is |
| Ribs | 0.4, 0.6, 0.8, 1.0, 1.2, 1.6 wide, 5 tall, x 61 + 6.8 i | thin walls |
| Slot gauge | 40.8 x 4.2 x 3 block, slots of the same widths, 2.4 deep | narrow gaps |
| 20 mm boss | 20.00 x 20.00 x 6, stands on the bed in a 26 x 26 window (tied to the plate on its back side) | x-y size, elephant foot, flat top, sharp corners |
| Fit pins | 5.00 mm pins in 10 mm collars (4 tall), radial clearance 0.10 / 0.15 / 0.20 / 0.25 / 0.30 / 0.40; pin first 0.4 mm narrowed by 0.3 so elephant foot cannot weld it; pins 7 mm tall | tightest working fit |
| Hole pad | 28 x 27 x 3, through holes 10, 3, 4, 5, 6 | hole size |
| Wordmark samples | "gnoshcodes" ascender height 3.5 / 3 / 2.5: raised 0.6 (left), engraved 0.6 in a 1.4 pad (right) | smallest legible text |
| Seam cylinder | d 10 x 8 | seam |
| Cone tip | d 8 -> point, 10 tall | cooling on small layers |
| String towers | two d 4 x 10, 22 mm apart | retraction |
| Bridges | spans 5, 10, 15, 20, 25 between 3 mm pillars, deck 0.8 thick at 4 mm above the plate | bridging |
| Hook set | gnoshcodes hook mark raised 0.6 at 10, 6, 3 mm tall | curve smoothness, small detail |
| Dot pins | d 2, 1.5, 1 x 1.2 tall | small circles, cooling |

All labels are raised 0.4 mm (or engraved 0.6 where stated), Liberation Sans Bold, 2-4.2 mm. The brand geometry is
the gnoshcodes lockup artwork (`assets/brand/*.svg`, both inks merged), never redrawn.

## Making a tile with your ID

`assets/tile.scad` (OpenSCAD 2021.01 or newer; needs `assets/brand/` next to it):

```
scripts/registry.py tile PLA-NAVY-01 --printer P1S [--layer 0.16] [--out tile.stl]
```

prints the `openscad` command with `PRINTER`, `FIL` and `LAYER` (text only) set, every argument quoted; run it as
printed. The code and printer must match the registry's patterns, so nothing in them is special to OpenSCAD or a
shell. `VER` is the tile's version; leave it. Leave `PRINTER` and `FIL` empty for the generic tile
(`assets/tuning-tile_generic.stl`). OpenSCAD prints the feature table as `ECHO: "FEAT;..."` lines on stderr.
It is shipped as `assets/features_generic.txt` and is the same for every `PRINTER` and `FIL`. If you change the
geometry, write a new one without rendering: `openscad -o features.echo tile.scad`.

## Checks (scripts/)

- `check_stl.py tile.stl --expect-shells 7` - size, 7 shells (tile + 6 pins), closed mesh.
- Slice it (Bambu Studio, or the bambu-studio-settings skill's `slice_check.py --keep`), then
  `check_gcode.py sliced.3mf assets/features_generic.txt` - every feature is in the G-code at its designed
  height, each bridge has a Bridge extrusion at its deck height. Size samples (2.5 mm text, 3 mm hook, 1 mm dot)
  are reported as info: if the slicer drops them, that is a reading, not a fault. Arcs (G2/G3, arc fitting on)
  are followed along the arc.
- `check_gcode.py` reads Bambu Studio 2.x G-code comments (`; Z_HEIGHT:`, `; FEATURE:` names such as `Bridge`,
  relative E). Checked on one real slice: P1S, 0.4 nozzle, 0.20 mm, PLA Basic, arc fitting on (10,488 arc
  moves); all 59 features passed. Other printers and Bambu Studio versions are not yet checked.

## Slicing notes (Bambu Studio command line)

- **PETG with the CLI:** set `curr_bed_type` (e.g. `Textured PEI Plate`) in the process JSON. Without it the CLI
  uses Cool Plate, where PETG presets have a plate temperature of 0, and it stops with "Filaments are not
  compatible with the plate type". PLA slices either way (but then at Cool Plate temperatures).
- **Flattening system presets for the CLI:** resolve `inherits`, then the preset's `include` templates, then the
  preset's own keys, in that order of priority. If inherited keys win over the include templates, a machine
  preset comes out with the generic start G-code of `fdm_machine_common` instead of the printer's own (and
  filament variant lists come out short).
