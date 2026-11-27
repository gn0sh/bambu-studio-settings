# Bambu Studio skills

Three free Agent Skills that set up Bambu Studio print settings and check them before you print. They run in an AI coding agent such as Claude Code.

| Skill | What it does |
|---|---|
| [`bambu-studio-settings`](bambu-studio-settings/) | Makes user presets and tunes project files (.3mf): plate-wide settings and per-object rules, checked before you print. Previews presets as a dry run and writes only new files, never over your originals. A new user preset syncs through your Bambu cloud account. |
| [`print-object-analyzer`](print-object-analyzer/) | Reads the real geometry of each part on a plate, or in STL files, and proposes print settings per object, with the measurement behind each one. Feeds `bambu-studio-settings`. Needs numpy. |
| [`telltile`](telltile/) | Telltile™: print one small test tile per filament, photograph it, and get suggested settings for that filament on your printer, kept in your own filament list. Checks the tile file and sliced G-code before you print. |

Each folder has its own README with install steps, safety notes and limits. To install one, copy its folder
into your agent's skills folder (for Claude Code: `~/.claude/skills/`), or upload its zip from the [Releases page](https://github.com/gn0sh/bambu-studio-settings/releases/latest)
as a skill.

## License

MIT, see `LICENSE`. Free to use, change and share; keep the copyright notice.

Telltile™ is a trademark of GNOSHCODES LLC. Bambu Lab and Bambu Studio are trademarks of Bambu Lab. This project is not affiliated with or endorsed by
Bambu Lab. It ships no Bambu Studio code or profiles: it reads the ones already on your computer.

A gnoshcodes project.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/brand/gnoshcodes-lockup-dark.svg">
  <img src="assets/brand/gnoshcodes-lockup-light.svg" alt="gnoshcodes" width="90">
</picture>
