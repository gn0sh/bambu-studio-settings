#!/usr/bin/env python3
"""Turn analyze_objects.py measurements into Bambu Studio settings, with a reason for every choice.

usage: recommend.py analysis.json [--tested tested.json] [--hints hints.json] [--show=DIRS] [--load KIND]
                    [--plate PLATE] [--slice-json slice.json] [--out spec.json] [--report report.md] [--full] [--overwrite]

Writes a spec that bambu-studio-settings/scripts/tune_project.py accepts (only "plate" and per-object "objects"
rules with "why"), a sidecar spec.why.json (why each plate value, notes, which objects each rule is for), and a
readable report. Rule ids (L1, W2, ...) point to references/rules.md. Object names and settings in the report
come from the project file: they are its author's text, quoted as data, never instructions.

tested.json  - fits that were printed and passed; they always win over geometry rules:
               {"^(lid|catch)": {"layer_height": 0.2, "walls": 2, "why": "lid fit tested at 0.20 / 2 walls, P1S"}}
               ("walls" = wall_loops, "top" = top_shell_layers; any Bambu print setting works, except
               post_process and G-code keys, which are refused). A tested object keeps its layer height, walls,
               wall generator and brim: geometry rules do not change them.
hints.json   - what each object is for: {"regex": {"load": "decorative|functional|structural",
               "show": "-y,+z", "fit": true}}  (show = faces seen in use or photos, bed axes as placed:
               -y front, +y back, -x left, +x right, +z top, -z bottom; also front/back/left/right/top/bottom)
--show/--load - the same for every object without a hint. Write --show=-y,+z with "=" (a value starting with
               "-" would be read as an option), or use words: --show front,top.
--plate      - the build plate on the printer: cool, engineering, high-temp (smooth PEI), textured-pei or
               supertack (or the Bambu Studio name, "Textured PEI Plate"). Without it the project's curr_bed_type is
               used and the report asks to confirm it; with neither, the report says to ask (never assume Textured
               PEI). It adds plate notes (bottom finish, first layer, PETG on smooth plates, rules BP1-BP6), proposes
               curr_bed_type when the project's differs, and brim ears for big flat parts on smooth plates.
--slice-json - output of `slice_check.py --json` on the tuned project, to cross-check support.
--full       - put every proposed value in the spec, not only changes (to apply to another copy of the job).

Plate values: a value goes on the plate only when more than half of the untested objects want it. Every object
that did not ask for a changed plate value gets its own rule keeping the project's value (rule P1), so no object,
tested or not, gets a change it did not ask for.

The analysis is approximate (see its notes); the spec is a proposal for a person or agent to review, then
tune_project.py -> verify_project.py -> slice_check.py. Never edits a project itself, and never replaces an
existing output file unless --overwrite.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import poa_common as pc  # noqa: E402
from poa_common import cell, md, safe_text  # noqa: E402

SOURCES = {"B": "Bambu default", "C": "common practice", "T": "tested here", "G": "geometry",
           "P": "project value kept"}
DIRWORDS = {"front": "-y", "back": "+y", "left": "-x", "right": "+x", "top": "+z", "bottom": "-z"}
ALIAS = {"walls": "wall_loops", "top": "top_shell_layers", "bottom": "bottom_shell_layers", "infill": "sparse_infill_density"}
DIMENSIONAL = {"layer_height", "wall_loops", "wall_generator", "detect_thin_wall", "elefant_foot_compensation",
               "initial_layer_print_height", "precise_z_height"}
TESTED_KEEP = DIMENSIONAL | {"brim_type", "brim_width", "brim_object_gap"}  # a tested fit depends on these too
SETTING_KEY = re.compile(r"^[A-Za-z0-9_]+\Z")  # \Z, not $: "key\n" is not a key
SPEC_SECTIONS = ("plate", "objects")  # what tune_project.py reads (it refuses anything else)
# Build plates: --plate word -> the curr_bed_type name Bambu Studio uses. Only Textured PEI is a textured surface.
PLATES = (("cool", "Cool Plate"), ("engineering", "Engineering Plate"), ("high-temp", "High Temp Plate"),
          ("textured-pei", "Textured PEI Plate"), ("supertack", "Supertack Plate"))
TEXTURED_PLATES = ("Textured PEI Plate",)


def first(v, default=None):
    if isinstance(v, list):
        return v[0] if v else default
    return default if v is None else v


def fnum(v, default):
    try:
        f = float(str(first(v, default)).rstrip("%"))
    except (TypeError, ValueError):
        return default
    return f if f == f and abs(f) != float("inf") else default


def fmtnum(v):
    """One spelling per value: 0.20 / '0.20' / '0.2' -> '0.2', 3 / '3' -> '3'; anything else unchanged."""
    if isinstance(v, bool):
        return "1" if v else "0"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f or abs(f) == float("inf"):
        return str(v)
    return ("%.4f" % f).rstrip("0").rstrip(".")


def parse_dirs(s):
    if not s:
        return set()
    items = s if isinstance(s, list) else re.split(r"[,\s]+", str(s))
    out = set()
    for x in items:
        x = str(x).strip().lower()
        if x:
            out.add(DIRWORDS.get(x, x))
    bad = out - {"+x", "-x", "+y", "-y", "+z", "-z"}
    if bad:
        raise pc.UserError("unknown show direction(s): %s" % ", ".join(safe_text(b, 20) for b in sorted(bad)))
    return out


def plate_name(s):
    """The Bambu Studio plate name for a --plate word or name (any case), or None."""
    t = str(s or "").strip().lower()
    for word, name in PLATES:
        if t in (word, name.lower()):
            return name
    return None


def plate_facts(name):
    """{"name", "smooth"} for a known plate name, or None (plate unknown)."""
    return None if name is None else {"name": name, "smooth": name not in TEXTURED_PLATES}


def load_json(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise pc.UserError("cannot read %s %s (%s)" % (what, path, e.strerror or e))
    except ValueError as e:
        raise pc.UserError("%s %s is not valid JSON (%s)" % (what, path, safe_text(str(e), 200)))


def load_table(path, what):
    """A tested.json / hints.json: {regex: {key: value}}; keys starting with _ are comments."""
    if not path:
        return {}
    t = load_json(path, what)
    if not isinstance(t, dict):
        raise pc.UserError("%s must be a JSON object of {regex: {...}}" % what)
    out = {}
    for rx, v in t.items():
        if rx.startswith("_"):
            continue
        if not isinstance(v, dict):
            raise pc.UserError("%s %r: the value must be an object" % (what, rx))
        try:
            out[rx] = (re.compile(rx, re.I), v)
        except re.error as e:
            raise pc.UserError("%s: bad regex %r (%s)" % (what, rx, e))
    return out


def check_tested(table):
    """tested.json holds print settings only: no post-processing scripts or G-code (L2), one value each."""
    for rx, (_, v) in table.items():
        for k, x in v.items():
            if k == "why":
                continue
            key = ALIAS.get(k, k)
            if not SETTING_KEY.match(key):
                raise pc.UserError("tested.json %r: %r is not a setting key (letters, digits and _ only)" % (rx, k))
            if key.lower() == "post_process" or "gcode" in key.lower():
                raise pc.UserError("tested.json %r: %s is refused: post-processing scripts and G-code are not print "
                                   "settings this skill writes (set them in Bambu Studio yourself)" % (rx, key))
            if isinstance(x, (dict, list)) or x is None:
                raise pc.UserError("tested.json %r: %s must be one string or number" % (rx, key))


def match_all(table, name):
    """Merge every entry of {regex: (compiled, dict)} whose regex matches name (later entries win)."""
    out, hits = {}, []
    for rx, (crx, v) in (table or {}).items():
        if crx.search(name):
            out.update(v)
            hits.append(rx)
    return out, hits


class Rec:
    def __init__(self):
        self.set = {}      # key -> (value str, why, rule id, source letter)
        self.notes = []

    def put(self, key, value, why, rule, src):
        self.set[key] = (fmtnum(value), why, rule, src)

    def note(self, text, rule=None):
        self.notes.append(("[%s] " % rule if rule else "") + text)


def recommend_object(o, ps, hint, tested, nozzle, bed=None):
    r = Rec()
    load = (str(hint.get("load") or "")).lower() or None
    show = parse_dirs(hint.get("show"))
    fit_hint = bool(hint.get("fit"))
    circles = o.get("circles", [])
    fitc = fit_hint or load in ("functional", "structural") or any(c["d_mm"] < 6.5 for c in circles)
    showy = bool(show) or load == "decorative"
    zf = o["z_features"]
    fits = zf["layer_fit"]
    isl = o["islands"]
    detail = (isl["most_small_islands_in_one_layer"] or 0) >= 5 or (isl["smallest_island_mm2"] or 99) < 1.0
    tested_keys = {ALIAS.get(k, k): v for k, v in tested.items() if k != "why"}
    twhy = str(tested.get("why", "tested settings"))

    # ---- tested settings win
    for k, v in tested_keys.items():
        r.put(k, v, "tested: " + twhy, "K1", "T")

    def free(key):
        return key not in tested_keys and not (tested_keys and key in TESTED_KEEP)

    # ---- layer height
    lo_lh, hi_lh = round(0.2 * nozzle, 2), round(0.7 * nozzle, 2)
    cands = [c for c in fits if lo_lh - 1e-9 <= float(c) <= hi_lh + 1e-9] or list(fits)
    if free("layer_height"):
        f20 = fits.get("0.20", {})
        if showy and detail:
            lh, why, rule = "0.12", "show object with fine detail (%d small islands in a layer, smallest %s mm2): finer " \
                "layers keep small features and text crisp" % (isl["most_small_islands_in_one_layer"],
                                                               isl["smallest_island_mm2"]), "L3"
        elif showy:
            lh, why, rule = "0.16", "show faces (%s): finer layers make walls and slopes smoother (Bambu's " \
                "0.16 mm Optimal / Balanced Quality presets)" % (",".join(sorted(show)) or "decorative"), "L2"
        elif fitc:
            order = sorted(cands, key=lambda c: (-fits[c]["exact"], abs(float(c) - 0.2), -float(c)))
            best = order[0]
            if fits[best]["exact"] == 0:
                best = "0.20" if "0.20" in cands else best
            lh = best
            why = "fit-relevant part: %d of %d flat z features land exactly on %s mm layers (max off %s mm)" % (
                fits[best]["exact"], fits[best]["of"], best, fits[best]["max_error_mm"])
            if zf.get("smallest_step_mm"):
                why += "; smallest z step %s mm" % zf["smallest_step_mm"]
            rule = "L1"
        else:
            lh, why, rule = "0.20", "no show faces or fit features marked: 0.20 mm is Bambu's Standard height (fastest " \
                "of the everyday heights)", "L0"
        if lh != "0.20" and f20 and f20["exact"] > fits[lh]["exact"]:
            why += "; note %d of %d z features land exactly at 0.20 but only %d at %s (they move up to %s mm)" % (
                f20["exact"], f20["of"], fits[lh]["exact"], lh, fits[lh]["max_error_mm"])
        r.put("layer_height", lh, why, rule, "C" if rule != "L0" else "B")

    # ---- walls
    w = o["walls"]
    sh = w["share_of_section_area"]
    if free("wall_loops"):
        if load == "structural":
            r.put("wall_loops", 4, "structural part: more perimeters carry most of the load", "W3", "C")
        elif showy:
            r.put("wall_loops", 3, "show faces: a third wall hides infill shadowing and smooths the outer wall", "W2", "C")
        else:
            r.put("wall_loops", 2, "hidden or functional part: 2 walls (Bambu default for 0.4 mm nozzle)", "W1", "B")
    if w["thinnest_mm"] is not None and w["thinnest_mm"] < 2 * w["line_mm"] and sh["1_to_2_lines"] + sh["lt_1_line"] > 0.005:
        r.note("thinnest wall ~%s mm (z %s): only 1-2 lines fit there whatever wall_loops says" % (
            w["thinnest_mm"], w["at_z"]), "W4")
    if (sh["lt_1_line"] > 0.003 or detail) and free("wall_generator") and first(ps.get("wall_generator"), "classic") != "arachne":
        r.put("wall_generator", "arachne", "features thinner than one line (%.1f%% of section area) or fine detail: "
              "Arachne varies line width instead of dropping them" % (100 * sh["lt_1_line"]), "W5", "C")

    # ---- top / bottom
    if load == "structural":
        r.put("top_shell_layers", max(5, int(fnum(ps.get("top_shell_layers"), 5))), "structural: solid skins", "T2", "C")
        r.put("bottom_shell_layers", max(4, int(fnum(ps.get("bottom_shell_layers"), 3))), "structural: solid skins", "T2", "C")
    elif "+z" in show and fnum(ps.get("top_shell_layers"), 5) < 5:
        r.put("top_shell_layers", 5, "top face shows: 5 top layers stop pillowing over infill", "T1", "C")

    # ---- overhangs and speed
    bands = {b["band_deg_from_vertical"]: b["area_mm2"] for b in o["overhangs"]["bands"]}
    steep = bands.get("45-60", 0) + o["overhangs"].get("steep_not_flat_mm2", 0)
    if showy and steep > 20:
        cur = fnum(ps.get("outer_wall_speed"), 200)
        r.put("outer_wall_speed", int(round(cur * 0.75)), "show part with %.0f mm2 of 45-72 deg overhang: a slower "
              "outer wall gives cleaner overhang edges (current %g mm/s)" % (steep, cur), "O1", "C")
    if (bands.get("30-45", 0) + steep) > 5 and str(first(ps.get("enable_overhang_speed"), "1")) == "0":
        r.put("enable_overhang_speed", 1, "overhangs present: Bambu slows down on overhangs by default", "O2", "B")
    if steep > 5:
        r.note("%.0f mm2 of steep overhang (45-72 deg from vertical): part cooling matters (overhang fan is a "
               "filament setting, not per object)" % steep, "O3")

    # ---- support
    br = o["bridges"]
    unsup = br["unsupported_flat_mm2"] or 0
    span = br["longest_span_mm"] or 0
    steep_nf = o["overhangs"].get("steep_not_flat_mm2", 0)
    need = unsup > 2 or steep_nf > 25 or span > 50
    cur_sup = str(first(ps.get("enable_support"), "0")) in ("1", "true")
    if need:
        why = "needs support: %s mm2 flat with no support at either end, %s mm2 at 60-72 deg, longest bridge %s mm" % (
            unsup, steep_nf, span)
        r.put("enable_support", 1, why, "S1", "G")
        small = unsup < 400
        r.put("support_type", "tree(auto)" if (showy or small) else "normal(auto)",
              ("show part: tree supports touch less of the surface and remove cleanly" if showy else
               "small unsupported areas: tree supports use little material and remove easily") if (showy or small)
              else "large flat ceilings: normal supports give a flatter underside", "S2", "C")
        regs = [x for x in br["regions"] if x["unsupported_mm2"] > 0]
        if regs and not any(x["solid_below_somewhere"] for x in regs):
            r.put("support_on_build_plate_only", 1, "every unsupported area has only the bed below it", "S3", "G")
    elif cur_sup:
        r.put("enable_support", 0, "nothing needs support: no unsupported flat areas, bridges up to %s mm" % span,
              "S1", "G")
    if span > 20:
        r.note("longest bridge %s mm: Bambu bridges this unsupported, but check sag; thick_bridges=1 is stronger, "
               "0 looks cleaner" % span, "S4")

    # ---- brim / first layer
    fl = o["first_layer"]
    htb = o.get("height_to_base") or 0
    brim_free = free("brim_type") and free("brim_width")
    if "-z" in show:
        r.note("bottom shows: no brim (brim leaves a scar on the bottom edge)", "B3")
    elif htb > 3 and o["height_mm"] > 15:
        if brim_free:
            r.put("brim_type", "outer_only", "tall and narrow: height is %.1fx the base width - a brim stops it "
                  "rocking loose" % htb, "B1", "C")
            r.put("brim_width", 5, "5 mm brim for a tall narrow part", "B1", "C")
        else:
            r.note("tall and narrow (height %.1fx the base width) would get a brim, but it is a tested part: add "
                   "brim_type to its tested.json entry if its brim should change" % htb, "B1")
    elif fl["contact_mm2"] < 100 and o["height_mm"] > 10:
        if brim_free:
            r.put("brim_type", "outer_only", "small bed contact (%s mm2) for a %s mm tall part" % (
                fl["contact_mm2"], o["height_mm"]), "B2", "C")
        else:
            r.note("small bed contact (%s mm2) would get a brim, but it is a tested part" % fl["contact_mm2"], "B2")
    if fl["contact_mm2"] > 20000:
        r.note("large first layer (%s mm2): watch corners lifting; clean plate, brim ears if they lift" %
               fl["contact_mm2"], "B4")
        if bed and bed["smooth"] and "-z" not in show and brim_free and "brim_type" not in r.set:
            r.put("brim_type", "brim_ears", "large first layer (%s mm2) on a smooth plate (%s): it grips less than "
                  "textured PEI, and brim ears hold the corners" % (fl["contact_mm2"], bed["name"]), "BP6", "C")
    # ---- build plate (--plate or the project's curr_bed_type)
    if "-z" in show:
        if bed is None:
            r.note("bottom face shows: its finish depends on the build plate (Textured PEI: matte and textured; "
                   "smooth plates: glossy) - ask which plate is on the printer", "BP4")
        else:
            r.note("bottom face shows: on the %s it comes out %s; pick the plate for the finish you want" % (
                bed["name"], "glossy and smooth" if bed["smooth"] else "matte and textured"), "BP4")
    types = ps.get("filament_type") if isinstance(ps.get("filament_type"), list) else [ps.get("filament_type")]
    petg = sorted({f for f in o.get("filaments") or [1] if isinstance(f, int) and 0 < f <= len(types)
                   and "PET" in str(types[f - 1] or "").upper()})
    if bed and bed["smooth"] and petg:
        r.note("PETG (filament %s) on a smooth plate (%s) can fuse to it and tear the surface: use a release agent "
               "(glue stick), or print it on Textured PEI" % (",".join(map(str, petg)), bed["name"]), "BP5")
    relief = any(abs(x["z"] - 0.3) < 0.02 or abs(x["z"] - 0.4) < 0.02 for x in zf["levels"] if x["down_mm2"] > 0) \
        and fl["contact_share_of_footprint"] < 0.99
    bed_circ = [c for c in circles if c["axis"] == "z" and c["from"] <= 0.5]
    if bed_circ:
        r.note("%d round hole/pin(s) start at the bed (d %s mm): elephant's foot squeezes them; keep "
               "elefant_foot_compensation (now %s)%s" % (
                   len(bed_circ), ", ".join(str(c["d_mm"]) for c in bed_circ[:4]),
                   safe_text(first(ps.get("elefant_foot_compensation"), "the profile's value"), 20),
                   "; the model also has a bottom-edge relief step" if relief else ""), "B5")
    if circles:
        r.note("round features near tolerance size: " + "; ".join(
            "%s-axis %s d%s" % (c["axis"], c["kind"], c["d_mm"]) for c in circles[:6]) +
            (" ..." if len(circles) > 6 else "") + " - holes print undersize, pins oversize by ~0.1-0.2 mm; a tested "
            "fit is only valid at the settings it was tested with", "F1")

    # ---- seam
    sides = show & {"+x", "-x", "+y", "-y"}
    if free("seam_position") and sides:
        if "+y" not in sides:
            r.put("seam_position", "back", "show sides %s: put the seam line on the hidden back (+Y)" %
                  ",".join(sorted(sides)), "M1", "C")
        else:
            r.put("seam_position", "aligned", "the back (+Y) shows too: one straight seam line; better, rotate the "
                  "object so a hidden side faces +Y and use 'back'", "M2", "C")
        if o.get("curved_wall_share", 0) > 0.3:
            r.put("seam_slope_type", "external", "%d%% of the walls are curved: a scarf seam hides the seam on smooth "
                  "round walls" % round(100 * o["curved_wall_share"]), "M3", "C")

    # ---- infill
    dens = fnum(ps.get("sparse_infill_density"), 15)
    if load == "structural":
        r.put("sparse_infill_density", "30%", "structural: 25-40% infill", "I3", "C")
        r.put("sparse_infill_pattern", "gyroid", "structural: gyroid is strong in every direction", "I3", "C")
    elif load == "functional" and dens < 15:
        r.put("sparse_infill_density", "15%", "functional part: Bambu's default 15%", "I2", "B")
    elif load == "decorative" and dens > 15:
        r.put("sparse_infill_density", "10%", "decorative part: little infill needed", "I1", "C")

    # ---- small layers / islands
    sl = o["small_layers"]
    if sl["count"] and sl["count"] >= 0.2 * sl["of"]:
        r.note("%d of %d sampled layers have under 25 mm2 to print (z %s-%s): the slicer slows to the filament's "
               "minimum layer time (slow_down_layer_time, now %s s); printing it with other parts helps cooling" % (
                   sl["count"], sl["of"], sl["z_range"][0], sl["z_range"][1],
                   safe_text(first(ps.get("slow_down_layer_time"), "profile's"), 20)), "N1")
    if detail:
        r.note("fine detail: %d small islands in one layer, smallest %s mm2 (text, pins, tips)" % (
            isl["most_small_islands_in_one_layer"], isl["smallest_island_mm2"]), "N2")
    if tested_keys:
        r.note("tested object: geometry rules did not change its layer height, walls or brim (%s)" % twhy, "K1")
    return r, {"load": load, "show": sorted(show), "fit_relevant": fitc}


def why_path(out):
    root, ext = os.path.splitext(out)
    return root + ".why" + (ext or ".json")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analysis")
    ap.add_argument("--tested")
    ap.add_argument("--hints")
    ap.add_argument("--show", help="show directions for objects without a hint, e.g. --show=-y,+z or --show front,top")
    ap.add_argument("--load", choices=["decorative", "functional", "structural"])
    ap.add_argument("--plate", help="build plate on the printer: " + ", ".join(w for w, _ in PLATES))
    ap.add_argument("--slice-json")
    ap.add_argument("--full", action="store_true",
                    help="write every proposed setting, even ones the project already has (a portable spec)")
    ap.add_argument("--out", help="spec JSON for tune_project.py (also writes SPEC.why.json next to it)")
    ap.add_argument("--report", help="markdown report (default: print)")
    ap.add_argument("--overwrite", action="store_true", help="replace output files that already exist")
    a = ap.parse_args()

    parse_dirs(a.show)
    inputs = [a.analysis, a.tested, a.hints, a.slice_json]
    pc.check_outputs([a.out, why_path(a.out) if a.out else None, a.report], inputs, a.overwrite)
    A = load_json(a.analysis, "analysis")
    if not isinstance(A, dict) or not isinstance(A.get("objects"), list):
        raise pc.UserError("%s is not an analyze_objects.py output" % a.analysis)
    ps = (A.get("project") or {}).get("settings", {})
    tested_t = load_table(a.tested, "tested.json")
    check_tested(tested_t)
    hints_t = load_table(a.hints, "hints.json")
    nozzle = fnum(ps.get("nozzle_diameter"), 0.4)
    if a.plate is not None and plate_name(a.plate) is None:
        raise pc.UserError("--plate %r is not a build plate: use %s" % (safe_text(a.plate, 40), ", ".join(
            w for w, _ in PLATES)))
    proj_bed_raw = first(ps.get("curr_bed_type"))
    proj_bed = plate_name(proj_bed_raw)
    bed = plate_facts(plate_name(a.plate) if a.plate is not None else proj_bed)
    plate_notes = []
    if a.plate is None and proj_bed is None:
        plate_notes.append("[BP0] build plate unknown%s: ask which plate is on the printer and run again with "
                           "--plate (%s). Never assume Textured PEI: the plate decides the bed temperature "
                           "(curr_bed_type)" % ((" (the project's curr_bed_type is %s)" % safe_text(proj_bed_raw, 40))
                                                if proj_bed_raw else "", ", ".join(w for w, _ in PLATES)))
    elif a.plate is None:
        plate_notes.append("[BP0] build plate taken from the project (curr_bed_type %s): confirm it is the plate on "
                           "the printer, or run again with --plate" % proj_bed)
    if bed and bed["smooth"]:
        plate_notes.append("[BP2] smooth plate (%s): glossy bottom face; less grip on sharp corners and small "
                           "footprints than Textured PEI, so brims matter more (B1, B2, BP6); first-layer squish "
                           "and elephant's foot read differently than on a textured plate" % bed["name"])
    elif bed:
        plate_notes.append("[BP3] Textured PEI: matte, textured bottom face; Bambu's start G-code lowers Z by "
                           "0.02 mm (0.01 mm on a 0.2 mm nozzle) to fill the texture, so first-layer squish and "
                           "elephant's foot measured here do not carry over to a smooth plate")
    objs = [o for o in A["objects"] if not o.get("error")]
    not_analyzed = [o for o in A["objects"] if o.get("error")]

    recs, ctx = {}, {}
    for o in objs:
        hint, _ = match_all(hints_t, o["name"])
        hint = dict(hint)
        hint.setdefault("show", a.show)
        hint.setdefault("load", a.load)
        tested, _ = match_all(tested_t, o["name"])
        r, c = recommend_object(o, ps, hint, tested, nozzle, bed)
        c["tested"] = bool(tested)
        recs[id(o)], ctx[id(o)] = r, c
    # objects that could not be measured get no proposals, but plate changes must not reach them either (P1)
    unmeasured = [o for o in not_analyzed if o.get("id") is not None]
    for o in unmeasured:
        recs[id(o)], ctx[id(o)] = Rec(), {"tested": False, "load": None, "show": [], "fit_relevant": False}

    # ---- one layer height on prime-tower plates with 2+ filaments (Bambu Studio cannot slice mixed heights there)
    problems = []
    # A5: a tested.json or hints.json pattern ignores case, so it cannot tell "Lid" from "LID" unless it says so
    folded = defaultdict(set)
    for o in objs + not_analyzed:
        if isinstance(o.get("name"), str):
            folded[o["name"].casefold()].add(o["name"])
    for names in (sorted(v) for v in folded.values() if len(v) > 1):
        if any(sum(1 for n in names if crx.search(n)) > 1 for t in (tested_t, hints_t) for crx, _ in t.values()):
            problems.append("objects %s differ only in letter case, and tested.json / hints.json patterns ignore "
                            "case: write (?-i:^Name\\Z) to match one of them only" % ", ".join(
                                safe_text(n, 60) for n in names[:5]))
    if str(first(ps.get("enable_prime_tower"), "0")) in ("1", "true"):
        by_plate = defaultdict(list)
        for o in objs:
            by_plate[o["plate"]].append(o)
        for pl, os_ in by_plate.items():
            fil = set()
            for o in os_:
                fil |= set(o["filaments"])
            if len(fil) < 2:
                continue
            tested_lh = {recs[id(o)].set["layer_height"][0] for o in os_
                         if ctx[id(o)]["tested"] and "layer_height" in recs[id(o)].set}
            if len(tested_lh) > 1:
                problems.append("plate %s: tested parts need different layer heights (%s) but the plate has a prime "
                                "tower with %d filaments - split them onto separate plates" % (
                                    pl, ", ".join(sorted(tested_lh)), len(fil)))
                continue
            if tested_lh:
                lh, why = tested_lh.pop(), "a tested part on this plate needs it"
            else:
                votes = Counter(recs[id(o)].set["layer_height"][0] for o in os_ if "layer_height" in recs[id(o)].set)
                if not votes:
                    continue
                lh = sorted(votes.items(), key=lambda kv: (-kv[1], abs(float(kv[0]) - 0.2)))[0][0]
                why = "most objects on the plate want it"
            for o in os_:
                r = recs[id(o)]
                old = r.set.get("layer_height")
                if old and old[0] != lh:
                    r.note("wanted %s mm (%s) but plate %s prints a prime tower with %d filaments, so every object "
                           "there gets one layer height: %s mm (%s). For %s mm, move it to its own plate" % (
                               old[0], old[1], pl, len(fil), lh, why, old[0]), "L5")
                r.put("layer_height", lh, "prime-tower plate with %d filaments: one layer height for all (%s)" % (
                    len(fil), why), "L5", "B")

    # ---- where the top lands at the final layer height
    for o in objs:
        r = recs[id(o)]
        lh_now = r.set.get("layer_height", (None,))[0] or str(first(ps.get("layer_height"), ""))
        fits = o["z_features"]["layer_fit"]
        key = "%.2f" % fnum(lh_now, 0) if lh_now else None
        if key in fits and abs(fits[key]["top_lands_at"] - o["height_mm"]) > 0.01:
            r.note("top lands at %s mm, not %s mm, at %s mm layers (Bambu's precise_z_height can fix this)" % (
                fits[key]["top_lands_at"], o["height_mm"], key), "L4")

    # ---- plate values: strict majority over untested objects; everyone else keeps the project's value (P1)
    def cur(key):
        return None if key not in ps else fmtnum(first(ps[key]))

    def own(o):
        return o.get("settings") or {}

    def keep_value(o, k):
        """The value object o has now for k (its own override, else the project's): what a P1 pin keeps."""
        return fmtnum(own(o)[k]) if k in own(o) else cur(k)

    def needs_pin(o, k, v):
        if recs[id(o)].set.get(k, (None,))[0] is not None:
            return False  # it asked for a value: either v, or its own rule already says otherwise
        if k in own(o) and not a.full:
            return False  # its own override already wins over the plate value
        return keep_value(o, k) != v

    keys = sorted({k for o in objs for k in recs[id(o)].set})
    plate, plate_why = {}, {}
    pool = [o for o in objs if not ctx[id(o)]["tested"]] or objs
    for k in keys:
        votes = Counter(recs[id(o)].set[k][0] for o in pool if k in recs[id(o)].set)
        if not votes:
            continue
        v, n = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0] != cur(k)))[0]
        if n * 2 <= len(pool):
            continue  # no strict majority: per object only
        if v == cur(k) and not a.full:
            continue
        if any(needs_pin(o, k, v) and keep_value(o, k) is None for o in objs + unmeasured):
            continue  # an object that did not ask for it cannot be kept at a value the project does not set
        plate[k] = v
        ex = next(o for o in pool if recs[id(o)].set.get(k, (None,))[0] == v)
        plate_why[k] = "%d of %d untested objects: %s [%s]" % (n, len(pool), recs[id(ex)].set[k][1],
                                                               recs[id(ex)].set[k][2])

    def plate_value(k):
        return plate.get(k, cur(k))

    rules, by_name, pins_of = [], {}, {}
    for o in objs + unmeasured:
        r = recs[id(o)]
        diff, whys, pins = {}, [], {}
        for k, (v, why, rule, src) in r.set.items():
            eff = plate_value(k) if a.full else own(o).get(k, plate_value(k))
            if eff is None or fmtnum(eff) != v:
                diff[k] = v
                whys.append("%s=%s: %s [%s, %s]" % (k, v, why, rule, SOURCES[src]))
        for k, v in plate.items():
            if needs_pin(o, k, v):
                kv = keep_value(o, k)
                pins[k] = (kv, "keeps %s: the plate changes to %s for the objects that asked for it, and this %s "
                               "object did not" % (kv, v, "tested" if ctx[id(o)]["tested"] else "untested"),
                           "P1", "P")
                diff[k] = kv
                whys.append("%s=%s: %s [P1, %s]" % (k, kv, pins[k][1], SOURCES["P"]))
        pins_of[id(o)] = pins
        by_name.setdefault(o["name"], []).append((diff, whys))
    # recommend/same-name-pin: a rule matches by name, so objects that share a name get one rule only when every
    # one of them needs the same changes (an object that needs none counts too); otherwise no rule, and a PROBLEM
    # a not-analyzed object without an id cannot be pinned, but the rule would still match its name
    unpinnable = {o.get("name") for o in not_analyzed if o.get("id") is None}
    for name, wants in by_name.items():
        diffs = [d for d, _ in wants]
        if name in unpinnable and any(diffs):
            problems.append("an object named %s could not be measured and has no id, and an analyzed object has the "
                            "same name: a spec rule matches by name and would also change it, so none was written; "
                            "rename one of them in Bambu Studio and run again" % safe_text(name, 80))
            continue
        if any(d != diffs[0] for d in diffs[1:]):
            keys_ = sorted({k for d in diffs for k in d if any(d2.get(k) != d.get(k) for d2 in diffs)})
            problems.append("%d objects are named %s but need different settings (%s): a spec rule matches by "
                            "name, so none was written for them; rename them in Bambu Studio and run again" % (
                                len(diffs), safe_text(name, 80), ", ".join(safe_text(k, 40) for k in keys_[:10])))
            continue
        if diffs[0]:
            rules.append({"names": [name], "set": diffs[0], "why": "; ".join(wants[0][1])})
    # objects with the same settings for the same reasons share one rule
    merged = []
    for rule in rules:
        twin_ = next((m for m in merged if m["set"] == rule["set"] and m["why"] == rule["why"]), None)
        if twin_:
            twin_["names"] += rule["names"]
        else:
            merged.append(rule)
    # A5: the exact names, in their case (tune_project matches with re.I; (?-i:...) turns that off), to \Z
    rules = [{"match": "(?-i:^(%s)\\Z)" % "|".join(re.escape(n) for n in m["names"]) if len(m["names"]) > 1
              else "(?-i:^%s\\Z)" % re.escape(m["names"][0]), "set": m["set"], "why": m["why"]} for m in merged]

    # ---- slicer cross-check
    xcheck = []
    if a.slice_json:
        S = load_json(a.slice_json, "slice JSON")
        S = S if isinstance(S, dict) else {}
        need_any = [o["name"] for o in objs if recs[id(o)].set.get("enable_support", ("0",))[0] == "1"]
        for p in S.get("plates") or []:
            if not isinstance(p, dict):
                continue
            sg = fnum(p.get("support_g"), 0)
            pl = safe_text(p.get("plate"), 10)
            if sg > 0 and not need_any:
                xcheck.append("plate %s: the slicer printed %.2f g of support but the analysis found nothing that needs "
                              "it - look at where (painted support? overhang the grid missed?)" % (pl, sg))
            elif sg == 0 and need_any:
                xcheck.append("plate %s: no support in the G-code, but the analysis says %s need it - check that "
                              "enable_support reached those objects" % (pl, ", ".join(safe_text(n, 60)
                                                                                     for n in need_any[:5])))
            else:
                xcheck.append("plate %s: slicer support %.2f g agrees with the analysis" % (pl, sg))
        for w_ in S.get("warnings") or []:
            xcheck.append("slicer warning: " + safe_text(w_, 300))

    # ---- build plate: a project setting only (no object pins), so it joins the plate values after the rules
    if a.plate is not None and (proj_bed != bed["name"] or a.full):
        plate["curr_bed_type"] = bed["name"]
        plate_why["curr_bed_type"] = "--plate: the plate on the printer (the project says %s) [BP1]" % (
            safe_text(proj_bed_raw, 40) if proj_bed_raw else "nothing")
    pp = (A.get("project") or {}).get("post_process_findings") or 0
    spec = {"plate": plate, "objects": rules}
    assert set(spec) <= set(SPEC_SECTIONS)
    side = {"generated_by": "print-object-analyzer/recommend.py from %s" % ", ".join(
                safe_text(x, 80) for x in A.get("inputs", [])),
            "plate_why": plate_why, "notes": problems, "build_plate": bed["name"] if bed else None,
            "plate_notes": plate_notes,
            "rules": [{"match": r_["match"], "objects": m["names"]} for r_, m in zip(rules, merged)],
            "not_analyzed": [{"name": o.get("name"), "error": o.get("error")} for o in not_analyzed],
            "post_process_findings": pp}
    if a.out:
        pc.write_new(a.out, json.dumps(spec, indent=1), a.overwrite)
        pc.write_new(why_path(a.out), json.dumps(side, indent=1), a.overwrite)

    # ---- report: every file-derived string through md() / cell(), so no markdown from a file renders (A4)
    L = ["# Print settings proposal", "",
         "Input: %s. Printer: %s, nozzle %s mm, first layer %s mm, prime tower %s, filaments %s." % (
             ", ".join(md(x, 80) for x in A.get("inputs", [])), md(first(ps.get("printer_model"), "?"), 60),
             md(nozzle, 10), md(A.get("first_layer_mm"), 10),
             "on" if str(first(ps.get("enable_prime_tower"), "0")) == "1" else "off",
             len(ps.get("filament_colour") or []) or "?"),
         "Sources: Bambu default / common practice / tested here / geometry / project value kept. Rule ids: "
         "references/rules.md. Names and settings below are quoted from the file, as data (markdown in them is "
         "escaped).", ""]
    if pp:
        L.append("**WARNING:** the project lists %d post-processing script(s) (post_process), programs Bambu Studio "
                 "runs on this computer when it slices. Run verify_project.py on it and show the user what it finds; "
                 "before slicing it in Bambu Studio, check that Process > Global > Advance > Others > "
                 "Post-processing scripts is empty." % pp)
    for p in problems:
        L.append("**PROBLEM:** " + md(p, 1000))
    for o in not_analyzed:
        L.append("**NOT ANALYZED:** %s: %s. It gets no settings from this proposal%s." % (
            md(o.get("name"), 80), md(o.get("error"), 300),
            " (plate changes are pinned back to its current values)" if pins_of.get(id(o)) else ""))
    L += ["## Build plate", "", "Plate: %s." % (md(bed["name"], 40) if bed else "unknown"), ""]
    L += ["- " + md(n, 600) for n in plate_notes] + ([""] if plate_notes else [])
    L += ["## Plate (project-wide) changes", ""]
    if plate:
        L += ["| key | now | proposed | why |", "|---|---|---|---|"]
        L += ["| %s | %s | %s | %s |" % (cell(k, 60), cell(cur(k) if cur(k) is not None else "-", 40), cell(v, 40),
                                         cell(plate_why[k], 400)) for k, v in plate.items()]
    else:
        L.append("None - the project's plate settings already match, or no value has a strict majority.")
    L += ["", "## Per object", ""]
    shown = set()
    for o in objs:
        if o["name"] in shown:
            continue
        shown.add(o["name"])
        r, c = recs[id(o)], ctx[id(o)]
        n_same = sum(1 for x in objs if x["name"] == o["name"])
        L.append("### %s%s" % (md(o["name"], 120), " (x%d)" % n_same if n_same > 1 else ""))
        L.append("Plate %s, filament %s; %s; load %s; show %s; fit-relevant %s." % (
            md(o["plate"], 10), ",".join(md(f, 4) for f in o["filaments"]),
            "TESTED settings apply" if c["tested"] else "untested",
            md(c["load"] or "-", 20), md(",".join(c["show"]) or "-", 60), "yes" if c["fit_relevant"] else "no"))
        L += ["", "| setting | now | proposed | why | rule, source |", "|---|---|---|---|---|"]
        rows = dict(r.set)
        rows.update(pins_of.get(id(o), {}))
        for k, (v, why, rule, src) in sorted(rows.items()):
            now = own(o).get(k, cur(k))
            now = "-" if now is None else now
            L.append("| %s | %s | %s | %s | %s, %s |" % (cell(k, 60), cell(now, 40), cell(v, 40), cell(why, 400),
                                                         rule, SOURCES[src]))
        for n in r.notes:
            L.append("- " + md(n, 600))
        L.append("")
    if xcheck:
        L += ["## Slicer cross-check", ""] + ["- " + md(x, 600) for x in xcheck] + [""]
    L += ["## Next", "", "Review, then: `tune_project.py in.3mf out.3mf --spec spec.json` -> `verify_project.py out.3mf` "
          "-> `slice_check.py out.3mf` (single filament) -> print -> record the settings each test passed at in "
          "tested.json.", ""]
    text = "\n".join(L)
    if a.report:
        pc.write_new(a.report, text, a.overwrite)
        print("wrote %s%s" % (a.report, (" and %s (+ %s)" % (a.out, why_path(a.out))) if a.out else ""))
    else:
        print(text)


if __name__ == "__main__":
    pc.run_main(main)
