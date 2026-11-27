# print-object-analyzer

An Agent Skill that reads the real geometry of each part on a
Bambu Studio plate, or in STL files, and proposes print settings per object. Every proposal comes with the
measurement behind it. It runs in an AI coding agent such as Claude Code and plugs into the
**bambu-studio-settings** skill (also from gnoshcodes), so the flow is:
analyze -> recommend -> review -> tune -> verify -> slice -> print -> record.

## What it measures

Per object, in its placed orientation:

- footprint, height, height-to-base ratio, first-layer contact area
- downward-facing area by overhang angle band, and at what height
- flat ceilings: bridges and their spans, or areas with no support at either end
- thinnest wall / smallest feature, and small islands (text, pins, tips)
- heights of flat features (step tops, ledges, catches), and which layer heights land exactly on them
- round holes and pins under 6.5 mm, vertical and horizontal
- area facing each direction (for show faces and the seam), share of curved walls

From these it proposes layer height, walls, top/bottom shells, outer-wall and overhang speed, supports, brim,
seam, infill and wall generator, and it factors in the build plate you name (`--plate`): bottom-face finish,
brims on smooth plates, release agent for PETG on smooth PEI, and the plate's `curr_bed_type`. It keeps your **tested** settings: a part whose fit already passed keeps the
settings it passed at. It also keeps the one-layer-height rule on prime-tower plates.

## What is in it

| Path | What it does |
|---|---|
| `SKILL.md` | The workflow the agent follows |
| `scripts/analyze_objects.py` | Measures every object of a .3mf project or STL files; text report + JSON |
| `scripts/recommend.py` | Turns measurements + your hints and tested fits into a `tune_project.py` spec and a report |
| `scripts/mesh_io.py` | STL / 3MF loading (component transforms, parts, plates, filament slots) |
| `scripts/poa_common.py` | Finding the bambu-studio-settings reader, quoting file text, safe output files |
| `tests/` | Regression tests (`python3 -I -m unittest discover -s tests`) |
| `references/rules.md` | Rule table: measurement -> setting -> why -> source; exact vs approximate; limitations |
| `assets/` | Example `tested.json` and `hints.json` |

Needs Python 3.8+ with numpy, and the **bambu-studio-settings** skill for reading .3mf projects: this skill reads
every .3mf through that skill's hardened project reader (`bambu_common.open_project`), so there is one reader for
untrusted project files. STL files need nothing else. Applying, verifying and slicing also use
bambu-studio-settings, and a Bambu Studio install.

## Install

Copy the `print-object-analyzer` folder into your agent's skills folder (for Claude Code: `~/.claude/skills/`),
next to `bambu-studio-settings` (both are needed for .3mf files), or upload the zip as a skill. If the two are not
side by side, set `PRINT_ANALYZER_BSS_DIR` to the bambu-studio-settings folder or pass `--bss-dir`. Then ask things like "what should this plate print
at? The drawer front shows in photos, and the lid was tested at 0.20 mm".

## Limits

- Grid-based numbers (wall thickness, bridge spans, contact width) are approximate: +/- one grid cell, spans
  along x/y only.
- It flags what needs support but does not run the slicer's own support logic. Cross-check with a slice.
- Which faces show, and what a part is for, come from you.
- The rules are common FDM practice and Bambu defaults. Your printer's test results should override them, and
  the skill is built for that.

## Safety

- The scripts read your files and write new ones; they never change their input, and never replace an existing
  output unless you pass `--overwrite`.
- Safety with downloaded files: a .3mf from the internet can carry post-processing scripts (programs that run on
  your computer when it is sliced), custom G-code, and text written to steer an AI agent. This is a settings
  tool, not a malware scanner: run bambu-studio-settings' `verify_project.py` on a downloaded project first; the
  analyzer warns when a project lists post-processing scripts and prints names and settings from the file only
  as quoted data (in report.md with markdown escaped, so a link or image in a name shows as plain text). The only sure check before you slice a downloaded project in Bambu Studio is Bambu Studio's own
  field: Process panel, Global, switch on Advance, Others tab, Post-processing scripts. It should be empty.

## License

MIT, see `LICENSE`. Bambu Lab and Bambu Studio are trademarks of their owners; this project is not affiliated
with or endorsed by Bambu Lab.

A gnoshcodes project.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/brand/gnoshcodes-lockup-dark.svg">
  <img src="../assets/brand/gnoshcodes-lockup-light.svg" alt="gnoshcodes" width="90">
</picture>
