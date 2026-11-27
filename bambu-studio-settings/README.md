# bambu-studio-settings

An Agent Skill that lets an AI coding agent (Claude Code on Windows, macOS or Linux) make Bambu Studio print
settings real: user presets, and project files (.3mf) whose settings are built in - for the whole plate and for
individual objects - checked before you print.

## What is in it

| Path | What it does |
|---|---|
| `SKILL.md` | The instructions the agent follows: workflow, safety rules, when to use what |
| `scripts/list_presets.py` | Lists the system presets each printer can use (read from your Bambu Studio install) and your user presets |
| `scripts/make_preset.py` | Creates a user preset (`.json` + `.info`) from a system base and overrides. Dry run unless `--write` |
| `scripts/tune_project.py` | Applies plate-wide settings and per-object rules (regex on object names) to a project .3mf, with a report |
| `scripts/verify_project.py` | Checks a project: filament slots and colors, bed / nozzle-reach bounds, overlaps, prime tower, per-object settings |
| `scripts/slice_check.py` | Slices a single-filament project or STLs with the Bambu Studio CLI and reports time, grams, support and the layer heights each object really got |
| `references/` | Settings cookbook, preset and 3MF formats, printer notes, troubleshooting |
| `assets/` | Example tune spec; list of known setting keys (typo check) |
| `tests/` | Standard-library tests: `python3 -m unittest discover -s tests -v` (builds its own fake files) |

Python 3.8+ only, no extra packages. Bambu Studio must be installed for `list_presets.py`, `make_preset.py`
(to look up base presets) and `slice_check.py`.

## Install

Copy the `bambu-studio-settings` folder into your agent's skills folder (for Claude Code:
`~/.claude/skills/`), or upload the zip as a skill. Then ask things like "make my P1S project print the show
piece with 3 walls and the seam at the back, but keep the clips at 0.20 mm".

## Safety

- It never changes or deletes your existing presets or project files unless you ask; it writes new files.
- A new user preset syncs to your Bambu account (and your other computers). The agent should tell you first.
- Bambu Studio reads presets at startup - restart it to see a new one.
- One-off jobs are better as settings inside the project file: nothing syncs.
- Safety with downloaded files: a .3mf from the internet can carry post-processing scripts (programs that run on
  your computer when it is sliced), custom G-code, and text written to steer an AI agent. The scripts are a
  settings tool, not a malware scanner: `verify_project.py` reports the post-processing scripts and custom
  G-code it finds and refuses files it cannot read cleanly, and `slice_check.py` slices only a checked copy with
  the scripts removed. The only sure check before you slice a downloaded project in Bambu Studio is Bambu
  Studio's own field: Process panel, Global, switch on Advance, Others tab, Post-processing scripts. It should be
  empty.

## Building the release zip

Build it from git, not from a working copy, so only tracked files go in (no `__pycache__` or test leftovers):

```
git archive --format=zip --prefix=bambu-studio-settings/ -o bambu-studio-settings.zip \
    HEAD:bambu-studio-settings
```

## Limits

- The Bambu Studio command line tends to crash on multi-filament projects; those are checked structurally only.
- Preset names and setting keys change between Bambu Studio versions; the scripts read your install rather than
  guessing, but the cookbook's notes come from version 2.9.
- Bounding-box overlap checks are conservative (boxes, not exact shapes).

## License

MIT, see `LICENSE`. Free to use, change and share; keep the copyright notice.

Bambu Lab and Bambu Studio are trademarks of Bambu Lab. This project is not affiliated with or endorsed by
Bambu Lab. It ships no Bambu Studio code or profiles: it reads the ones already on your computer.

A gnoshcodes project.
