# Draft: r/ClaudeAI (from u/gnoshcodes)

Do not post from this file without Brian's yes.

## Before posting
- [ ] Read r/ClaudeAI's current rules on self-promotion and on AI disclosure; use the flair they require (for example a "Built with Claude" or project-sharing flair) and follow the disclosure rule.
- [ ] The repo https://github.com/gn0sh/bambu-studio-settings is public, and the link opens while logged out.
- [ ] Post a few days after the r/BambuLab post, not the same day. One post only.
- [ ] Be ready to answer comments the same day.

## Title
Three Agent Skills for 3D printing: Bambu Studio presets, project files and a test tile

## Body
We made three Agent Skills for Bambu Lab printers and put them on GitHub under the MIT license. Each is a folder you copy into `~/.claude/skills/`, or a zip you upload as a skill on claude.ai.

- **bambu-studio-settings**: Python scripts the agent calls to make user presets and tune project files (.3mf), plate-wide and per object, then verify them. Standard library only.
- **print-object-analyzer**: reads the geometry of each part and proposes settings, each with its measurement. It uses numpy.
- **telltile**: the agent reads photos of a printed test tile and suggests one setting change at a time, then records it per filament.

Design choices that may help if you write skills: the scripts do the exact work (zip and XML handling, bounds checks) so the agent does not improvise it; every write goes to a new file and presets are a dry run until `--write`; text inside downloaded .3mf files and photos is treated as data, never as instructions; and each skill has its own tests that build fake files.

Limits: Bambu Studio's command line often crashes on multi-filament projects, so those are checked structurally only.

https://github.com/gn0sh/bambu-studio-settings

We are gnoshcodes, not affiliated with Bambu Lab. We would like feedback on how the skills trigger and where the instructions are unclear.
