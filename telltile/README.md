# Telltile™

An Agent Skill for the **gnoshcodes tuning tile**: print one tile, photograph it with your phone, and get told which Bambu Studio setting to change, one at a time. The agent (such as Claude Code) reads your photos, then keeps a record of what each of your filaments needs on each of your printers.

The tile (112 x 84 mm, about 52-59 min with printer start-up and 18 g, no supports, 0.4 nozzle, 0.20 mm layers) carries labelled
tests for overhangs (30-70 deg), bridges (5-25 mm), stringing, print-in-place fit (0.10-0.40 mm), size (20 mm
boss, 10 mm and 3-6 mm holes), thin walls and slots (0.4-1.6 mm), seam, a pointed tip, top surface, first layer
and elephant foot, plus fine-detail tests made from the gnoshcodes mark: the wordmark at 3.5 / 3 / 2.5 mm raised
and engraved, the hook mark at 10 / 6 / 3 mm and its dot as 2 / 1.5 / 1 mm pins.

## What is in it

| Path | What it does |
|---|---|
| `SKILL.md` | The instructions the agent follows: print, photograph, analyze, change one thing, reprint, record |
| `references/reading-guide.md` | For every feature: what good and bad look like in a photo, the cause, the Bambu Studio setting and a starting step |
| `references/photo-instructions.md` | The photo list to send the maker, and how to check the photos |
| `references/registry.md` | The filament registry format; which settings belong in a filament preset and which in the process |
| `references/tile-spec.md` | Every feature's size, making a tile with your printer and filament on the ID panel, checks, slicer notes |
| `scripts/registry.py` | Your filament registry: add filaments, record tile prints, print tuned settings as JSON for project files or presets. Results are kept per printer + build plate + filament |
| `scripts/check_stl.py` | Checks an exported tile: size, shells, closed mesh |
| `scripts/check_gcode.py` | Checks a sliced tile: every feature present at its designed height |
| `assets/tuning-tile_generic.stl` | The tile, ready to print (write your filament on the blank strip of the ID panel) |
| `assets/tile.scad`, `assets/brand/` | OpenSCAD source; set `PRINTER` and `FIL` to engrave your own ID |
| `assets/renders/` | What a perfect tile looks like: top, three-quarter, close-ups |
| `assets/filaments.blank.json` | An empty registry to start from |
| `assets/features_generic.txt` | The tile's feature table for `check_gcode.py` (same for every ID) |
| `tests/` | Standard-library tests: `python3 -m unittest discover -s tests -v` (builds its own fake files) |

Python 3.8+ only, no extra packages. Bambu Studio is needed only to check preset names and to slice.
Works best together with the **bambu-studio-settings** skill, which writes the settings into Bambu Studio
project files and user presets.

## Install

Copy the `telltile` folder into your agent's skills folder (for Claude Code: `~/.claude/skills/`), or
upload the zip as a skill. Then say "I printed the tuning tile in my red PLA on my P1S, here are the photos".

Companion skill: **bambu-studio-settings** (install it the same way, next to this one). It slices the tile,
lists exact preset names and writes tuned values into project files and presets. Without it, everything here
still works: you slice in Bambu Studio and type the recommended values in yourself.

## Notes

- Your filament registry is your own `filaments.json`, kept with your projects; the skill never stores it.
- The agent recommends one change per reprint. Expect 1-3 tiles per filament.
- A filament or process user preset syncs to your Bambu account; settings in a project file do not.
- Safety: the agent treats text in photos, files and registries as data, not instructions. `registry.py` accepts
  only the tuning settings and value ranges listed in `references/registry.md`, refuses `post_process` and G-code
  settings, and refuses the whole file if anything in it is off its schema. If someone shares a registry with
  you, run `registry.py validate` on it before you use it.
- Telltile™ is a trademark of GNOSHCODES LLC. Bambu Lab and Bambu Studio are trademarks of their owners; this project is not affiliated with or endorsed by
  Bambu Lab.

## License

MIT, see `LICENSE`. The license covers the code, model and docs; it grants no rights to the gnoshcodes name or
marks. The name and marks on the tile identify gnoshcodes; please keep them on copies.

A gnoshcodes project.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/brand/gnoshcodes-lockup-dark.svg">
  <img src="../assets/brand/gnoshcodes-lockup-light.svg" alt="gnoshcodes" width="90">
</picture>
