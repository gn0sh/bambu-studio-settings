# Draft: r/BambuLab (from u/gnoshcodes)

Do not post from this file without Brian's yes.

## Before posting
- [ ] Read r/BambuLab's current rules on self-promotion and on AI disclosure; follow them exactly (flair, ratio, disclosure line if required).
- [ ] The repo https://github.com/gn0sh/bambu-studio-settings is public, and the link opens while logged out.
- [ ] LICENSE and the three READMEs are on `main`; the release zip is attached.
- [ ] Be ready to answer comments the same day. One post only.

## Title
Free, MIT-licensed skills that set up Bambu Studio settings per plate and per object, and check them before you print

## Body
We made three free Agent Skills for people who print on Bambu Lab machines. They run in an AI coding agent such as Claude Code, and they work on the files Bambu Studio already uses.

- **bambu-studio-settings** makes user presets and tunes project files (.3mf): plate-wide settings and per-object rules, then checks filament slots, bed bounds, overlaps and the prime tower before you print. It can also slice a copy with the Bambu Studio command line to report time, grams and the layer heights each object got.
- **print-object-analyzer** measures each part on a plate (overhangs, bridges, thin walls, small holes) and proposes settings with the measurement behind each one.
- **telltile** is a small test tile. Print it in a filament and photograph it. The agent reads the photos, suggests one setting change at a time, and keeps a record per filament and printer.

The skills write new files only. Preset creation is a dry run until you add `--write`. A new user preset syncs through your Bambu account, so the agent should tell you first; settings inside a project file do not sync.

Limits: the Bambu Studio command line tends to crash on multi-filament projects, so those are checked structurally only. Our notes come from Bambu Studio 2.9.

Code and install steps: https://github.com/gn0sh/bambu-studio-settings

We are gnoshcodes. This is not affiliated with Bambu Lab. If something is wrong for your printer, or you want a setting covered, tell us here or open an issue.
