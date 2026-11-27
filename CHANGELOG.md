# Changelog

## v1.1.0 - build plate handling

The build plate decides which bed temperature Bambu Studio uses. All three skills now ask which plate is on the
printer, and none of them assumes Textured PEI.

- **bambu-studio-settings:** `verify_project.py` prints the build plate (`curr_bed_type`) and the bed temperature,
  first layer and other layers, it gives each filament. A temperature of 0 for that plate (the filament is not
  made for it, and the CLI refuses to slice it), a missing temperature or an unknown plate name is a PROBLEM. A
  project without `curr_bed_type` gets a note: the CLI then uses Cool Plate. `tune_project.py` warns about the
  same and still writes. The settings cookbook has a new "Build plate" section: the per-plate keys, which
  filaments a plate suits, how the first layer and the bottom finish differ on textured and smooth plates, and
  PETG on smooth PEI.
- **print-object-analyzer:** new `recommend.py --plate` option (cool, engineering, high-temp, textured-pei,
  supertack). It puts `curr_bed_type` in the spec when the project has another plate and adds notes on the
  bottom-face finish, the first layer and a release agent for PETG on smooth plates. It also proposes brim ears
  for large flat parts on smooth plates. Without `--plate` the project's plate is used, and the report asks you
  to confirm it. If neither is known, the report says to ask (rules BP0-BP6).
- **telltile:** results belong to printer + plate + filament. `registry.py init` no longer defaults to Textured
  PEI: you must name the plate on the printer. A printer's plate must be one of the five Bambu Studio plate
  names. Each record now carries the plate it was printed on, and a tile printed on another plate cannot change
  that printer's tuned values. `spec` sets `curr_bed_type` from the printer's plate, and `validate` flags tuned
  values that cite a test printed on another plate. Old registries whose records have no plate are still valid.
  A registry whose printer plate is not one of the five names is refused, and the message lists the names. The
  reading guide covers asking which plate and how first-layer gauges read on textured and smooth plates.

## v1.0.0

First release of the three skills.
