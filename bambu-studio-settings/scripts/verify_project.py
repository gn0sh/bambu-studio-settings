#!/usr/bin/env python3
"""Check a Bambu Studio project .3mf without slicing it: filament slots and colours, positions on the bed,
overlaps, and the per-object settings Bambu Studio will use. Use it for every project you build or tune, and in
place of a CLI slice for multi-filament projects (the CLI tends to crash on those).

usage: verify_project.py project.3mf [--area X0,Y0,X1,Y1] [--min-gap MM] [--expect-slots N] [--profiles DIR] [--json]

Checks
  slots     every object/part filament number exists in the project (filament_colour list); prints colour + preset
  bounds    each object's world bounding box sits inside the plate's printable area (bed shape from the project,
            or --area). On printers with per-nozzle reach (H2D-style extruder_printable_area) an object is also
            checked against the area its filaments' nozzles can reach, when filament_map is Manual.
  height    top of each object <= printable_height
  overlaps  bounding boxes on the same plate closer than --min-gap (default 0.8 mm) are flagged (boxes, not meshes:
            an L-shape nested in another can be flagged when it is fine - look before you move things)
  settings  object- and part-level overrides, and the effective layer height; prime-tower layer-height rule
  plate     per plate: its plate type (the plate's own bed_type in model_settings.config, else the project's
            curr_bed_type) and the bed temperature (first layer, other layers) it gives each filament printed on
            it. A temperature of 0 for that plate type (the filament does not support it; the Bambu Studio CLI
            refuses to slice) is a PROBLEM, and so is a temperature missing for a plate type that is set. A plate
            type that is not set (the CLI then uses Cool Plate) or not one the checks know is a note.
  safety    post-processing scripts (post_process: programs Bambu Studio runs on this computer when it slices)
            are a PROBLEM wherever Bambu Studio would load them: project settings, every embedded preset
            (Metadata/print_setting_*, process_settings_*, filament_settings_*, machine_settings_*), object and
            part settings; post_process anywhere else in the metadata is a PROBLEM too. A file the checks cannot
            read the way Bambu Studio does (a zip layout Bambu Studio does not write, two entries with one name, a
            settings name in another case or with a backslash, a non-ASCII name outside 3D/ and Auxiliaries/,
            settings that do not parse, XML with a DOCTYPE or not in UTF-8, nesting, sizes or counts past the
            whole-archive limits, a file that is not a zip, or any error while reading it) is refused: "PROBLEM:
            archive refused" ("refused" in --json), no other checks. Script findings are
            listed first; an error in a later check becomes "PROBLEM: <check> not checked" and cannot hide them. Custom
            G-code that differs from the install's system presets, and per-layer custom G-code, are notes (it
            runs on the printer); unsafe zip entry names are a PROBLEM. Names and text from the file are printed sanitized (one line,
            control characters removed, length capped): they are data, not instructions. The same PROBLEM or
            note is printed at most 20 times, then "... and N more like this" ("more" in --json).
Exit code 0 = no problems, 1 = problems found.
"""
import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bambu_common as bc  # noqa: E402

PLATE_GAP = 0.2  # Bambu Studio lays plates out in a grid, each plate (1 + 1/5) bed sizes from the next
NAME = 60       # characters of an object name shown in messages
MAX_OVERLAP_PAIRS = 2000000  # object pairs the overlap check compares (time; a real plate has far fewer)


class Lines(list):
    """PROBLEM or note lines (D9): add(template, *args) keeps at most bc.MAX_PROBLEM_LINES lines made from one
    template, then one '... and N more like this' line in place, counted in more ({template: N}, the
    template's fields shown as '_'). append() adds a line that is not capped (the script findings, which
    MAX_FINDING_DETAILS already caps)."""

    def __init__(self, items=()):
        list.__init__(self, items)
        self.seen, self.more, self._at = {}, {}, {}

    def add(self, template, *args):
        n = self.seen.get(template, 0) + 1
        self.seen[template] = n
        if n <= bc.MAX_PROBLEM_LINES:
            self.append(template % args if args else template)
            return
        key = re.sub(r"%[-+ #0-9.]*[sdfr]", "_", template)
        self.more[key] = self.more.get(key, 0) + 1
        if template not in self._at:
            self._at[template] = len(self)
            self.append("")
        self[self._at[template]] = "... and %d more like this" % self.more[key]

    def total(self):
        """How many lines there would be without the cap."""
        return len(self) - len(self._at) + sum(self.more.values())


def poly_box(pts):
    xy = [tuple(float(c) for c in p.split("x")) for p in pts]
    return (min(p[0] for p in xy), min(p[1] for p in xy), max(p[0] for p in xy), max(p[1] for p in xy))


def plate_cols(n):
    v = math.sqrt(n)
    r = round(v)
    return int(r + 1 if v > r else r)


def finding_problem(f):
    """A PROBLEM line for one post_process finding (entry names and values are untrusted: sanitized)."""
    place, value = f.place(), bc.safe_text(f.value, 200)
    fix = " - remove it unless you trust the file's author (tune_project.py --strip-post-process)"
    if f.fmt == "gcode-comment" and f.strippable and f.value:
        return ("post-processing script (post_process) in %s: PrusaSlicer runs it as a program on this computer "
                "when it slices (Bambu Studio does not load this file today): %s%s" % (place, value, fix))
    if f.fmt in ("json", "ini") and f.strippable and f.value:
        return ("post-processing script (post_process) in %s: Bambu Studio runs it as a program on this computer "
                "when it slices: %s%s" % (place, value, fix))
    if f.fmt == "xml" and f.on_object and f.strippable:
        return "%s has an object-level post_process setting: %s%s" % (place, value, fix)
    return ("%s mentions post_process where the checks cannot tell what Bambu Studio does with it%s - treat "
            "the file as unsafe to slice" % (place, (": " + value) if value else ""))


def script_problems(index):
    """PROBLEM lines for every post_process finding and for unsafe entry names. These come first and cannot
    fail, so no later check can hide a found script (F3)."""
    problems = [finding_problem(f) for f in index.findings]
    if index.more_findings:
        problems.append("... and %d more post_process findings" % index.more_findings)
    if index.unsafe:
        problems.append("the archive has unsafe entry names (Bambu Studio never writes these; tune_project.py leaves "
                        "them out): %s" % ", ".join(bc.safe_text(n, 80) for n in index.unsafe[:10]))
    return problems


def gcode_notes(index, ps, sp_loader):
    """Notes (Lines) about custom G-code (it runs on the printer) in the project settings, the embedded presets
    and per-layer custom G-code (Metadata/custom_gcode_per_layer.xml: a count and the first few)."""
    notes = Lines()
    if any(k.endswith("_gcode") for k in ps):
        sp = sp_loader()
        differs, unchecked = bc.custom_gcode(ps, sp)
        if differs:
            notes.add("custom G-code that differs from the system preset: %s - it runs on the printer; read it "
                      "before printing a project from someone else", ", ".join(bc.safe_text(k, 60) for k in differs))
        if unchecked:
            why = "no Bambu Studio install found (--profiles)" if sp is None else "the preset is not in the install"
            notes.add("G-code not compared with the system presets, %s: %s - read it before printing a project "
                      "from someone else", why, ", ".join(bc.safe_text(k, 60) for k in unchecked[:20]))
    for _, kind, d, raw in index.presets:
        keys = sorted(k for k, v in d.items() if isinstance(k, str) and k.endswith("_gcode") and bc.gcode_text(v))
        if keys:
            notes.add("G-code in the embedded %s preset %s, not compared with the system presets: %s - it runs on "
                      "the printer; read it before printing a project from someone else",
                      kind.replace("_", " "), bc.safe_text(raw, 80),
                      ", ".join(bc.safe_text(k, 60) for k in keys[:20]))
    for name, count, first in index.layer_gcode:
        shown = "; ".join("z=%s: %s" % (bc.safe_text(z, 20), bc.safe_text(t, 80)) for z, t in first)
        notes.add("custom G-code at %d layer(s) in %s: %s%s - it runs on the printer at that height; read it "
                  "before printing a project from someone else", count, bc.safe_text(name, 80), shown,
                  "; ..." if count > len(first) else "")
    return notes


def safety_checks(index, ps, sp_loader):
    """(problems, notes) about content that runs code: post_process, unsafe entry names, custom G-code."""
    return script_problems(index), gcode_notes(index, ps, sp_loader)


def checked(problems, check, fn, *args):
    """Run one check; an error in it becomes 'PROBLEM: <check> not checked: <reason>' and the others go on (F3)."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - any error in a later check must not hide the safety problems
        problems.add("%s not checked: %s", check, bc.safe_text(str(e) or type(e).__name__, 300))
        return None


def refused(a, reason):
    """Report an archive open_project refused, and exit 1."""
    reason = bc.safe_text(reason, 400)
    if a.json:
        print(json.dumps({"project": bc.safe_text(os.path.basename(a.project), 80), "refused": reason,
                          "problems": ["archive refused: " + reason], "notes": []}, indent=1))
    else:
        print("PROBLEM: archive refused: %s" % reason)
        print("1 problem(s)")
    sys.exit(1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project")
    p.add_argument("--area", help="X0,Y0,X1,Y1 area every object must sit in (e.g. a colour-reachable area)")
    p.add_argument("--min-gap", type=float, default=0.8, help="flag boxes closer than this (mm, default 0.8)")
    p.add_argument("--expect-slots", type=int, help="fail unless the project has exactly this many filaments")
    p.add_argument("--profiles", help="install's resources/profiles folder, to compare G-code with the system "
                                      "presets (auto-detected if omitted)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    force_area = None
    if a.area:
        try:
            force_area = tuple(float(x) for x in a.area.split(","))
        except ValueError:
            raise bc.BambuError("--area wants X0,Y0,X1,Y1 (four numbers), got %r" % a.area)
        if len(force_area) != 4:
            raise bc.BambuError("--area wants X0,Y0,X1,Y1 (four numbers), got %r" % a.area)

    if not os.path.isfile(a.project):
        raise bc.BambuError("No such file: %s" % a.project)
    try:
        index = bc.open_project(a.project)
    except bc.RefusedArchive as e:
        refused(a, e.reason)
    except Exception:  # noqa: BLE001 - L3: no input may end in a traceback or without --json output
        if os.environ.get("BSS_DEBUG"):
            raise
        refused(a, "internal error")
    with index:
        report(a, index, force_area)


def read_geometry(index, problems):
    main_model = bc.norm_name(bc.MAIN_MODEL)
    if main_model not in index.models:
        raise bc.BambuError("not a 3MF project (no 3D/3dmodel.model)")
    try:
        return index.geometry()
    except bc.GeometryError as e:
        problems.add("geometry not checked: %s", bc.safe_text(str(e), 300))
        return None


def layout_checks(a, index, ps, geo, force_area, st, problems, notes):
    """Bed, nozzle reach, height, overlaps and the prime tower; fills st (the summary's layout fields)."""
    main_model = bc.norm_name(bc.MAIN_MODEL)
    colours, nfil = st["colours"], len(st["colours"])
    fmap, fmode = st["fmap"], st["fmode"]
    bed = st["bed"] = poly_box(ps.get("printable_area") or ["0x0", "256x0", "256x256", "0x256"])
    height = float(ps.get("printable_height") or 1e9)
    ext_areas = st["ext_areas"] = [poly_box(s.split(",")) for s in (ps.get("extruder_printable_area") or []) if s]
    ex_pts = [q for q in (ps.get("bed_exclude_area") or []) if q]
    excl = poly_box(ex_pts) if len(ex_pts) >= 3 else None

    objs = bc.parse_objects(index.model_settings)
    by_id = {o["id"]: o for o in objs}
    plate_of, plates_meta = bc.parse_plates(index.model_settings)
    nplates = st["nplates"] = max([1] + list(plate_of.values()))
    st["objs"], st["plate_of"] = objs, plate_of
    cols = plate_cols(nplates)
    sx, sy = (bed[2] - bed[0]) * (1 + PLATE_GAP), (bed[3] - bed[1]) * (1 + PLATE_GAP)

    rows = st["rows"]
    for at in (geo.items.get(main_model, []) if geo else []):
        oid = at.get("objectid", "")
        if not bc.ID_RE.match(oid):
            problems.add("a build item has no objectid")
            continue
        if at.get("printable") == "0":
            notes.add("object %s is set not printable", oid)
        o = by_id.get(oid, {"id": oid, "name": "object %s" % oid, "extruder": "1", "settings": {}, "parts": []})
        name = bc.safe_text(o["name"], NAME)
        try:
            box = geo.bbox(main_model, oid, bc.tf(at.get("transform")))
        except bc.GeometryError as e:
            problems.add("%s: geometry not checked, %s", name, e)
            continue
        if box is None:
            problems.add("object %s has no geometry", oid)
            continue
        pl = plate_of.get(oid, 1)
        i = pl - 1
        ox, oy = (i % cols) * sx, -(i // cols) * sy
        bb = [box[0] - ox, box[1] - ox, box[2] - oy, box[3] - oy, box[4], box[5]]
        fil = sorted(bc.object_filaments(o))
        for f in fil:
            if f > nfil:
                problems.add("%s uses filament %d but the project has only %d", name, f, nfil)
        area, why = force_area, "--area"
        if area is None:
            area, why = bed, "bed"
            if ext_areas and fmode.lower().startswith("manual") and fmap:
                exts = sorted({bc.to_int(fmap[f - 1], 0) for f in fil if f - 1 < len(fmap)})
                boxes = [ext_areas[e - 1] for e in exts if 0 < e <= len(ext_areas)]
                if boxes:
                    area = (max(b[0] for b in boxes), max(b[1] for b in boxes), min(b[2] for b in boxes), min(b[3] for b in boxes))
                    why = "reach of nozzle(s) %s" % ",".join(map(str, exts))
            elif ext_areas and len(fil) > 1:
                notes.add("%s: filament_map_mode is %r, so which nozzle prints which filament is decided at slice "
                          "time; for safety keep multi-colour objects inside the area both nozzles reach",
                          name, bc.safe_text(fmode, 30))
        tol = 0.01
        inside = bb[0] >= area[0] - tol and bb[1] <= area[2] + tol and bb[2] >= area[1] - tol and bb[3] <= area[3] + tol
        if not inside:
            problems.add("%s (plate %d) is outside the %s %s: x %.1f..%.1f y %.1f..%.1f", name, pl, why, area,
                         *bb[:4])
        if excl and bb[0] < excl[2] and bb[1] > excl[0] and bb[2] < excl[3] and bb[3] > excl[1]:
            problems.add("%s (plate %d) overlaps the bed's excluded area %s", name, pl, excl)
        if bb[5] > height + tol:
            problems.add("%s is %.1f mm tall; printable height is %.1f", name, bb[5], height)
        if abs(bb[4]) > 0.05:
            notes.add("%s bottom is at z=%.2f (Bambu Studio drops it to the bed on load)", name, bb[4])
        parts = [(bc.safe_text(pt["name"], NAME), bc.safe_text(pt["subtype"], 20),
                  bc.safe_text(pt.get("extruder") or "(object)", 10), bc.safe_tree(pt["settings"], 100))
                 for pt in o["parts"]]
        rows.append({"plate": pl, "id": oid, "name": name, "filaments": fil,
                     "colours": [bc.safe_text(colours[f - 1], 20) if f - 1 < nfil else "?" for f in fil],
                     "bbox": [round(x, 2) for x in bb], "area": why, "inside": inside,
                     "layer_height": bc.safe_text(bc.effective(o, ps, "layer_height"), 12),
                     "object_settings": bc.safe_tree(o["settings"], 100), "parts": parts})

    by_plate = {}
    for r in rows:
        by_plate.setdefault(r["plate"], []).append(r)
    left = MAX_OVERLAP_PAIRS  # D9: the pair loop is quadratic, so it has a fixed budget
    for pl, prs in sorted(by_plate.items()):
        n = len(prs) * (len(prs) - 1) // 2
        if n > left:
            problems.add("plate %d: overlaps not checked, %d objects are more than this check compares", pl, len(prs))
            continue
        left -= n
        for i in range(len(prs)):
            for j in range(i + 1, len(prs)):
                A, B = prs[i]["bbox"], prs[j]["bbox"]
                g = max(B[0] - A[1], A[0] - B[1], B[2] - A[3], A[2] - B[3])
                if g < a.min_gap:
                    problems.add("plate %d: %s and %s are %s (box gap %.1f mm)", pl, prs[i]["name"], prs[j]["name"],
                                 "overlapping" if g < 0 else "close", g)
    if str(ps.get("enable_prime_tower")) == "1":
        plate_fils = dict((pl, {f for q in prs for f in q["filaments"]}) for pl, prs in by_plate.items())
        wx, wy = ps.get("wipe_tower_x") or [], ps.get("wipe_tower_y") or []
        try:
            w = float(ps.get("prime_tower_width") or 35)
        except ValueError:
            w = 35.0
        for r in rows:
            i = r["plate"] - 1
            if i < len(wx) and i < len(wy):
                tx, ty = float(wx[i]), float(wy[i])
                B = r["bbox"]
                if B[0] < tx + w and B[1] > tx and B[2] < ty + w and B[3] > ty and len(plate_fils[r["plate"]]) > 1:
                    notes.add("plate %d: %s may sit under the prime tower (tower at %.0f,%.0f, about %.0f mm square; "
                              "its real depth depends on the flush volume)", r["plate"], r["name"], tx, ty, w)
    st["layout_ok"] = True


def settings_checks(ps, st, problems, notes):
    """The prime-tower layer-height rule and filaments no object uses."""
    colours = st["colours"]
    for pl, msg in bc.layer_height_check(st["objs"], ps, st["plate_of"]):
        problems.add("plate %s: %s", pl, msg)
    used = st["used"] = sorted({f for r in st["rows"] for f in r["filaments"]})
    for f in (range(1, len(colours) + 1) if st["layout_ok"] else ()):  # rows are only complete after layout
        if f not in used:
            notes.add("filament %d (%s) is in the project but no object uses it", f, bc.safe_text(colours[f - 1], 20))


def plate_checks(index, ps, st, fpresets, problems, notes):
    """The plate type of each plate (its own bed_type, else the project's curr_bed_type) and the bed temperatures
    it resolves to, per filament printed on that plate (every slot if the layout check did not finish); fills
    st["build_plates"]."""
    _, meta = bc.parse_plates(index.model_settings)
    if st["layout_ok"] and st["rows"]:
        slots = {}
        for r in st["rows"]:
            slots.setdefault(r["plate"], set()).update(r["filaments"])
        plates = sorted(slots)
    else:
        plates = sorted(meta)[:bc.MAX_BED_PLATES + 1] or [1]   # one over the cap, so bed_check says it cut
        every = set(range(1, min(len(st["colours"]), bc.MAX_BED_SLOTS) + 1))
        slots = dict((n, every) for n in plates)
    infos, errors, warnings = bc.bed_check(ps, [(n, meta.get(n, {}).get("bed_type"), sorted(slots[n]))
                                                for n in plates], fpresets)
    st["build_plates"] = infos
    for e in errors:
        problems.add("%s", e)
    for w in warnings:
        notes.add("%s", w)


def print_build_plates(plates, st_):
    """The text report's plate type and bed temperature lines."""
    deg = lambda v: "?" if v is None else "%d C" % v  # noqa: E731
    for bp in plates:
        src = {"plate bed_type": "the plate's bed_type", "curr_bed_type": "curr_bed_type"}.get(bp["source"])
        shown = bp["bed_type"] if bp["source"] == "plate bed_type" else bp["curr_bed_type"]
        print("  plate %d build plate: %s" % (bp["plate"], ("%s (%s)" % (st_(shown, 40), src)) if src else
                                              "not set - the CLI uses %s" % bp["bed"]))
        for t in bp["temps"]:
            print("  plate %d slot %d bed on %s: %s first layer, %s other layers (%s_initial_layer / %s)" % (
                bp["plate"], t["slot"], bp["bed"], deg(t["first_layer"]), deg(t["other_layers"]), bp["key"],
                bp["key"]))


def as_list(v):
    return v if isinstance(v, list) else []


def report(a, index, force_area=None):
    ps = index.project_settings or {}
    sp_cache = []

    def sp_loader():
        if not sp_cache:
            sp_cache.append(bc.load_system_presets(a.profiles))
        return sp_cache[0]

    # F3: post_process findings and unsafe names first, then G-code notes; every later check is wrapped, so an
    # error in one (a printable_area that is not numbers, say) becomes a PROBLEM and cannot hide a found script.
    problems = Lines(script_problems(index))
    notes = checked(problems, "custom G-code", gcode_notes, index, ps, sp_loader) or Lines()
    st = {"colours": as_list(ps.get("filament_colour")), "fmap": as_list(ps.get("filament_map")),
          "fmode": str(ps.get("filament_map_mode", "")), "bed": None, "ext_areas": [], "nplates": 1, "rows": [],
          "objs": [], "plate_of": {}, "used": [], "layout_ok": False, "build_plates": []}
    fpresets = as_list(ps.get("filament_settings_id"))
    if a.expect_slots and len(st["colours"]) != a.expect_slots:
        problems.add("project has %d filaments, expected %d", len(st["colours"]), a.expect_slots)
    geo = checked(problems, "geometry", read_geometry, index, problems)
    checked(problems, "layout", layout_checks, a, index, ps, geo, force_area, st, problems, notes)
    checked(problems, "settings", settings_checks, ps, st, problems, notes)
    checked(problems, "build plate", plate_checks, index, ps, st, fpresets, problems, notes)

    colours, fmap, fmode, ext_areas, used = st["colours"], st["fmap"], st["fmode"], st["ext_areas"], st["used"]
    summary = {"project": os.path.basename(a.project), "printer": ps.get("printer_settings_id"),
               "process": ps.get("print_settings_id"), "layer_height": ps.get("layer_height"),
               "prime_tower": ps.get("enable_prime_tower"), "support": ps.get("enable_support"),
               "filaments": [{"slot": i + 1, "colour": c, "preset": fpresets[i] if i < len(fpresets) else None,
                              "nozzle": fmap[i] if i < len(fmap) else None} for i, c in enumerate(colours)],
               "filament_map_mode": fmode, "build_plates": st["build_plates"], "bed": st["bed"],
               "plates": st["nplates"], "objects": st["rows"],
               "post_process": [f.as_dict() for f in index.findings], "more_findings": index.more_findings,
               "problems": list(problems), "notes": list(notes)}
    summary["more"] = dict(problems.more)  # D9: {template: lines not printed}
    summary["more"].update(notes.more)
    summary = bc.safe_tree(summary, 2000)  # every string from the file: one line, no control characters
    if a.json:
        print(json.dumps(summary, indent=1))
    else:
        st_ = bc.safe_text
        print("%s: %s | %s | layer %s | prime tower %s | support %s" % (st_(summary["project"], 80), st_(summary["printer"], 80),
              st_(summary["process"], 80), st_(summary["layer_height"], 12), st_(summary["prime_tower"], 8),
              st_(summary["support"], 8)))
        if str(ps.get("enable_prime_tower")) == "1" and len(used) > 1:
            print("  prime tower at x %s y %s (width %s) - check it does not sit on an object" % (
                st_(ps.get("wipe_tower_x"), 60), st_(ps.get("wipe_tower_y"), 60), st_(ps.get("prime_tower_width"), 12)))
        for f in summary["filaments"]:
            print("  slot %d %s %s%s" % (f["slot"], st_(f["colour"], 20), st_(f["preset"], 80),
                                          (" -> nozzle %s" % st_(f["nozzle"], 8)) if ext_areas else ""))
        print_build_plates(summary["build_plates"], st_)
        if ext_areas:
            print("  filament_map_mode: %s; nozzle reach %s" % (st_(fmode, 30), ext_areas))
        for r in summary["objects"]:
            print("plate %d %-34s slots %-7s x %6.1f..%6.1f y %6.1f..%6.1f z %5.2f..%6.2f %s layer %s" % (
                r["plate"], r["name"][:34], ",".join(map(str, r["filaments"])), *r["bbox"],
                "IN " if r["inside"] else "OUT", r["layer_height"]))
            if r["object_settings"]:
                print("        object settings: %s" % json.dumps(r["object_settings"]))
            for pn, sub, ex, sts in r["parts"]:
                if len(r["parts"]) > 1 or sts or sub != "normal_part":
                    print("        part %-28s %-13s filament %s%s" % (pn[:28], sub, ex, (" settings " + json.dumps(sts)) if sts else ""))
        for n in summary["notes"]:
            print("note: " + n)
        for pr in summary["problems"]:
            print("PROBLEM: " + pr)
        print("OK - no problems found" if not problems else "%d problem(s)" % problems.total())

    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    bc.run_main(main)
