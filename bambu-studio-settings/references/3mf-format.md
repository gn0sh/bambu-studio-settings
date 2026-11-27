# Bambu project .3mf format (what the scripts read and write)

A .3mf is a zip. Read it with Python `zipfile`; write a new zip (never edit in place). A project saved by
Bambu Studio 2.x typically holds:

| Entry | What it is |
|---|---|
| `[Content_Types].xml`, `_rels/.rels` | Standard 3MF package files |
| `3D/3dmodel.model` | Build: one `<item objectid=... transform=...>` per object instance; objects may be meshes or `<components>` pointing into `3D/Objects/object_N.model` via `p:path` |
| `3D/Objects/object_N.model`, `3D/_rels/3dmodel.model.rels` | The meshes (production-extension layout) |
| `Metadata/project_settings.config` | JSON: the full plate/project settings (process + filament + machine keys, resolved) |
| `Metadata/model_settings.config` | XML: objects, their parts, per-object / per-part settings, filament slot per part, plates |
| `Metadata/plate_N.png`, `top_N.png`, `pick_N.png` ... | Thumbnails |
| `Metadata/slice_info.config`, `plate_N.gcode`, `plate_N.json` | Only after slicing: results and G-code |
| `Metadata/cut_information.xml`, `filament_sequence.json`, `layer_heights_profile.txt`, `layer_config_ranges.xml` | Optional extras |

## project_settings.config (plate-wide settings)

A JSON object of string values / string lists, e.g. `"layer_height": "0.2"`, `"wall_loops": "2"`,
`"filament_colour": ["#F5EBD4", "#0F1F3D", "#D2561B"]`. Useful identity keys:
`printer_settings_id`, `print_settings_id`, `filament_settings_id` (one per filament), `printer_model`,
`nozzle_diameter`, `printable_area`, `printable_height`, `extruder_printable_area` (per nozzle, H2D-type
printers), `filament_map` (filament -> nozzle, 1-based) and `filament_map_mode` (`Manual`, `Auto For Flush`,
`Auto For Match`).

- The number of filaments = length of `filament_colour` (and of the other per-filament lists). Adding a filament
  means extending every per-filament list consistently - easiest is to start from a project saved with the right
  number of filaments for that printer.
- Changing a value here changes the whole project. Keep the file complete: Bambu Studio and the CLI use it as the
  full configuration. Build new projects from a complete config saved by the same printer + Studio version
  (strip nothing).
- `print_settings_id` etc. only name the presets the project came from; the values in this file are what slices.
- `different_settings_to_system` is the app's own bookkeeping of what differs from the presets; you can leave it.

## model_settings.config (objects, parts, per-object settings, plates)

```xml
<config>
  <object id="3">
    <metadata key="name" value="lid (2 color)"/>
    <metadata key="extruder" value="1"/>          <!-- object's default filament slot, 1-based -->
    <metadata key="wall_loops" value="3"/>        <!-- a per-object setting override -->
    <metadata key="layer_height" value="0.2"/>
    <part id="1" subtype="normal_part">
      <metadata key="name" value="lid body"/>
      <metadata key="extruder" value="1"/>        <!-- this part's filament slot -->
    </part>
    <part id="2" subtype="normal_part">
      <metadata key="name" value="inlay"/>
      <metadata key="extruder" value="2"/>
    </part>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <model_instance>
      <metadata key="object_id" value="3"/>
      <metadata key="instance_id" value="0"/>
    </model_instance>
  </plate>
</config>
```

- `<object id>` matches the object id in `3D/3dmodel.model`'s `<build><item objectid=...>`. Part ids match the
  component objects (meshes) of that object, in order.
- **Per-object settings**: any `<metadata key="<setting>" value="..."/>` directly in `<object>` (before the
  parts). `tune_project.py` adds them right after the object's name line and removes older copies of the same key.
- **Per-part settings**: the same inside a `<part>`. Part subtypes: `normal_part`, `modifier_part`
  (settings for the region it overlaps), `negative_part`, `support_blocker`, `support_enforcer`.
- **Filament slots**: `extruder` on the object (default) and on each part (overrides). 1-based index into the
  project's filament lists. Painted multi-color (`paint_color` in the mesh) is separate and not handled here.
- Values must be XML-escaped (`&amp;`, `&quot;`); the scripts do that.
- A project with only `3D/3dmodel.model` + `Metadata/model_settings.config` + `Metadata/project_settings.config`
  opens fine; Bambu Studio regenerates thumbnails when it saves.

## Plates and positions

- Each `<plate>` lists the objects on it. Objects on plate N are placed in world coordinates offset by the plate's
  grid position: plates sit in a grid `cols = ceil-ish(sqrt(plate count))` wide (Bambu: round(sqrt(n)), plus one if
  sqrt(n) is above that), each `bed size * 1.2` apart: plate index i (0-based) is at
  `x + (i % cols) * width * 1.2`, `y - (i // cols) * depth * 1.2`. For a 350 mm wide bed, plate 2 starts at
  x = 420. `verify_project.py` undoes this offset before checking bed bounds.
- 3MF transforms are 3x4 row-major in row-vector form: `m00 m01 m02 m10 m11 m12 m20 m21 m22 tx ty tz`;
  a point maps as `x' = m00*x + m10*y + m20*z + tx` (and so on). Component transforms apply before the item's.
- Put the object's lowest point at z = 0 (Bambu Studio drops objects onto the bed on load anyway).

## Building a multi-part, multi-color object from STL files

1. One mesh `<object>` per STL part in `3D/3dmodel.model` (vertices deduplicated, triangles indexed), then one
   object with `<components>` listing those parts, then a `<build><item>` with the placement transform.
2. In `model_settings.config`, one `<object>` for the assembly with a `<part>` per component, each with its
   `extruder` slot.
3. A complete `project_settings.config` for the printer with as many filaments as slots, and `filament_colour`
   set (otherwise the app shows its own default colors), plus `filament_map` / `filament_map_mode` on two-nozzle
   printers.
4. Run `verify_project.py`; for multi-color plates do not rely on the CLI - ask the user to slice it once.

## After slicing (CLI `--export-3mf`)

`Metadata/slice_info.config` has per plate `prediction` (seconds), `weight` (g), `support_used`, `outside`, the
objects (`identify_id` = the G-code label id) and filament use. `Metadata/plate_N.gcode` carries comments the
scripts read: `; FEATURE: <type>`, `; CHANGE_LAYER`, `; Z_HEIGHT: z`, `; LAYER_HEIGHT: h`,
`; start printing object, unique label id: N`, header lines `; total estimated time`, `; filament_density`,
`; filament_diameter`. Extrusion is relative (`M83`), so a feature's filament use is the sum of its positive `E`.
