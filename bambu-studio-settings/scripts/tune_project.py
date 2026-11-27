#!/usr/bin/env python3
"""Tune a Bambu Studio project .3mf: plate-wide settings plus per-object overrides, with a report.

usage: tune_project.py in.3mf out.3mf --spec spec.json [--set key=value ...] [--object 'REGEX:key=value,key=value']
                       [--force] [--dry-run] [--overwrite] [--strip-post-process] [--profiles DIR]

spec.json:
  {"plate":   {"seam_position": "back", "top_shell_layers": 5},
   "objects": [{"match": "regex on the object name", "set": {"wall_loops": 3}, "why": "show face"},
               {"match": "^clip", "set": {"layer_height": 0.2}, "why": "tested at 0.20 mm"}],
   "clear":   [{"match": "regex", "keys": ["wall_loops"]}]}

* plate settings go into Metadata/project_settings.config (scalars are repeated for list settings);
* object settings become <metadata key=... value=.../> lines on the object in Metadata/model_settings.config,
  which Bambu Studio shows (and slices with) as that object's own settings; they take one value each;
* rules apply in order and every matching rule applies (later rules win on the same key);
* old sliced G-code inside the project is dropped (it would no longer match the settings), together with the
  slice results that point to it;
* zip entries with unsafe names (absolute, "..", backslashes, drive letters, control or format characters) are
  left out and reported.
Refuses to write when a plate has the prime tower on, 2+ filaments and objects at different layer heights
(Bambu Studio cannot slice that); --force writes anyway.
Never edits in.3mf. Refuses an out.3mf that already exists unless --overwrite, and never writes over in.3mf
(also not through a link or a differently cased name). Writes a temp file next to out.3mf, then moves it in place.
Refuses a project it cannot read the way Bambu Studio does (see verify_project.py). Warns when the project has
post-processing scripts (programs Bambu Studio runs when it slices) in its settings, embedded presets, object
settings or other metadata; --strip-post-process removes every one from out.3mf and checks out.3mf again before
it is kept, and refuses (writes nothing) when one cannot be removed exactly, such as post_process in an object name.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bambu_common as bc  # noqa: E402

SCALARS = (str, int, float, bool)
SLICE_INFO = bc.norm_name("Metadata/slice_info.config")
MODEL_RELS = bc.norm_name("Metadata/_rels/model_settings.config.rels")
PS_NORM = bc.norm_name(bc.PROJECT_SETTINGS)
MS_NORM = bc.norm_name(bc.MODEL_SETTINGS)
PLATE_JSON = re.compile(r"^metadata/plate_[0-9]+\.json\Z")


def parse_kv(s, where):
    if "=" not in s:
        raise bc.BambuError("%s wants key=value, got %r" % (where, s))
    k, v = s.split("=", 1)
    k, v = k.strip(), v.strip()
    if v.startswith("["):
        try:
            v = json.loads(v)
        except ValueError as e:
            raise bc.BambuError("%s: %s=%s is not a valid JSON list (%s)" % (where, k, v, e))
    return k, v


def check_value(k, v, where, allow_list):
    if isinstance(v, SCALARS):
        return
    if allow_list and isinstance(v, list) and all(isinstance(x, SCALARS) for x in v):
        return
    raise bc.BambuError("%s: %s must be %s, got %s" % (
        where, k, "a string, number or list of those" if allow_list else "one string or number (object settings "
        "take one value)", type(v).__name__))


def compile_rx(rx, where):
    if not isinstance(rx, str):
        raise bc.BambuError("%s: match must be a string (a regex on the object name)" % where)
    try:
        return re.compile(rx, re.I)
    except re.error as e:
        raise bc.BambuError("%s: bad regex %r (%s)" % (where, rx, e))


def load_spec(path):
    try:
        spec = bc.load_json(path)
    except OSError as e:
        raise bc.BambuError("cannot read spec %s (%s)" % (path, e.strerror or e))
    except ValueError as e:
        raise bc.BambuError("spec %s is not valid JSON (%s)" % (path, e))
    if not isinstance(spec, dict):
        raise bc.BambuError("spec %s must be a JSON object with plate / objects / clear" % path)
    extra = sorted(set(spec) - {"plate", "objects", "clear"})
    if extra:
        raise bc.BambuError("spec %s has unknown sections: %s (use plate, objects, clear)" % (path, ", ".join(extra)))
    if not isinstance(spec.get("plate", {}), dict):
        raise bc.BambuError("spec: plate must be an object of key: value")
    for sec in ("objects", "clear"):
        if not isinstance(spec.get(sec, []), list) or not all(isinstance(r, dict) for r in spec.get(sec, [])):
            raise bc.BambuError("spec: %s must be a list of objects" % sec)
    return spec


def build_rules(spec, object_args):
    rules = []
    for i, r in enumerate(spec.get("objects", [])):
        where = "spec objects[%d]" % i
        if "match" not in r or "set" not in r:
            raise bc.BambuError("%s needs both \"match\" and \"set\"" % where)
        if not isinstance(r["set"], dict):
            raise bc.BambuError("%s: set must be an object of key: value" % where)
        rules.append({"match": r["match"], "rx": compile_rx(r["match"], where), "set": dict(r["set"]),
                      "why": str(r.get("why", r["match"]))})
    for s in object_args:
        where = "--object %r" % s
        rx, sep, kvs = s.partition(":")
        if not sep or not kvs.strip():
            raise bc.BambuError("%s wants REGEX:key=value[,key=value]" % where)
        rules.append({"match": rx, "rx": compile_rx(rx, where),
                      "set": dict(parse_kv(x, where) for x in kvs.split(",") if x.strip()), "why": "--object"})
    clears = []
    for i, c in enumerate(spec.get("clear", [])):
        where = "spec clear[%d]" % i
        keys = c.get("keys")
        if "match" not in c or not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise bc.BambuError("%s needs \"match\" and \"keys\" (a list of setting names)" % where)
        clears.append({"match": c["match"], "rx": compile_rx(c["match"], where), "keys": keys})
    return rules, clears


def check_keys(plate, rules, clears, a):
    for k in list(plate) + [k for r in rules for k in r["set"]] + [k for c in clears for k in c["keys"]]:
        if not bc.SETTING_KEY_RE.match(k):
            raise bc.BambuError("%r is not a valid setting key (letters, digits and _ only)" % k)
    for k, v in plate.items():
        check_value(k, v, "plate", True)
    for r in rules:
        for k, v in r["set"].items():
            check_value(k, v, "rule %r" % r["match"], False)
    if bc.post_process_scripts(plate):
        raise bc.BambuError("tune_project.py does not add post-processing scripts (post_process); "
                            "set them in Bambu Studio yourself if you really want them")
    if any(bc.ascii_lower(k) == "post_process" for r in rules for k in r["set"]):
        raise bc.BambuError("post_process is not an object setting")
    wanted = set(plate) | {k for r in rules for k in r["set"]}
    known = bc.known_keys()
    if wanted - known:
        sp = bc.load_system_presets(a.profiles)
        if sp is not None:
            known |= bc.known_keys(sp)
    unknown = sorted(wanted - known)
    if unknown and not a.allow_unknown_keys:
        raise bc.BambuError("Unknown setting keys (typo?): %s - pass --allow-unknown-keys if you are sure. "
                            "Known keys come from assets/known_setting_keys.txt and the Bambu Studio install, "
                            "not from the project." % ", ".join(unknown))


def strip_slice_refs(ms):
    """model_settings.config bytes with plate gcode_file references emptied (their G-code is dropped)."""
    tree = bc.xml_tree(ms, "Metadata/model_settings.config")
    return bc.apply_edits(ms, [(m.start, m.end, b'<metadata key="gcode_file" value=""/>') for m in tree.iter("metadata")
                               if m.attrs.get("key") == "gcode_file"])


def strip_gcode_rels(rels):
    """The model settings rels file without its relationships to .gcode files."""
    tree = bc.xml_tree(rels, "Metadata/_rels/model_settings.config.rels")
    return bc.drop_elements(rels, [r for r in tree.iter("Relationship")
                                   if bc.ascii_lower(r.attrs.get("Target", "")).endswith(".gcode")])


def strip_slice_plates(si):
    """slice_info.config without its <plate> results."""
    return bc.drop_elements(si, list(bc.xml_tree(si, "Metadata/slice_info.config").iter("plate")))


def stale_entry(norm):
    """Old slice output, by normalised name (as Bambu Studio matches it): Metadata/*.gcode, *.gcode.md5,
    plate_N.json."""
    if not norm.startswith("metadata/"):
        return False
    return norm.endswith(".gcode") or norm.endswith(".gcode.md5") or bool(PLATE_JSON.match(norm))


def indent_of(ms, e):
    line = ms[ms.rfind(b"\n", 0, e.start) + 1:e.start]
    return line.decode("ascii") if line and not line.strip(b" \t") else "    "


def tune_objects(ms, rules, clears, report):
    """model_settings.config bytes with the rules applied: span edits on each object's own <metadata> lines."""
    tree = bc.xml_tree(ms, "Metadata/model_settings.config")
    edits = []
    for o in bc.model_objects(tree):
        metas = [c for c in o.children if c.tag == "metadata"]
        names = [m for m in metas if m.attrs.get("key") == "name"]
        if not names:
            continue
        nm = names[-1]  # Bambu Studio applies metadata in order: the last name is the one it shows
        name = nm.attrs.get("value", "")
        applied, whys = {}, []
        for c in clears:
            if c["rx"].search(name):
                for k in c["keys"]:
                    applied[k] = None
                whys.append("clear")
        for r in rules:
            if r["rx"].search(name):
                for k, v in r["set"].items():
                    applied[k] = bc.as_str(v)
                whys.append(r["why"])
        for m in metas:
            if m is not nm and m.attrs.get("key") in applied:
                edits.append(bc.line_span(ms, m) + (b"",))
        sets = {k: v for k, v in applied.items() if v is not None}
        if sets:
            indent = indent_of(ms, nm)
            add = "".join('\n%s<metadata key="%s" value="%s"/>' % (indent, k, bc.xml_attr(v)) for k, v in sets.items())
            edits.append((nm.end, nm.end, add.encode("utf-8")))
        report.append((name, applied, "; ".join(whys) or "plate settings"))
    return bc.apply_edits(ms, edits)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--spec", help="JSON spec (see above)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="plate setting; repeatable")
    ap.add_argument("--object", action="append", default=[], metavar="REGEX:KEY=VALUE[,KEY=VALUE]",
                    help="per-object rule; repeatable")
    ap.add_argument("--allow-unknown-keys", action="store_true")
    ap.add_argument("--profiles", help="install's resources/profiles folder, for the known-key check "
                                       "(auto-detected if omitted)")
    ap.add_argument("--force", action="store_true", help="write even if the layer-height check fails")
    ap.add_argument("--overwrite", action="store_true", help="replace out.3mf if it already exists")
    ap.add_argument("--strip-post-process", action="store_true",
                    help="remove post-processing scripts (post_process) from out.3mf")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    a = ap.parse_args()

    if bc.same_file(a.src, a.dst):
        raise bc.BambuError("out.3mf is the same file as in.3mf (keep the original); pick a new name.")
    if os.path.lexists(a.dst) and not a.overwrite and not a.dry_run:
        raise bc.BambuError("%s already exists; not replacing it (pass --overwrite to replace it)." % a.dst)
    spec = load_spec(a.spec) if a.spec else {}
    plate = dict(spec.get("plate", {}))
    for s in a.set:
        k, v = parse_kv(s, "--set")
        plate[k] = v
    rules, clears = build_rules(spec, a.object)
    check_keys(plate, rules, clears, a)

    with bc.open_project(a.src) as index:
        tune_index(a, index, plate, rules, clears)


def tune_index(a, index, plate, rules, clears):
    if index.project_settings is None or index.model_settings is None:
        raise bc.BambuError("%s has no Metadata/project_settings.config or model_settings.config - open it in "
                            "Bambu Studio and save it as a project first." % a.src)
    ps, ms = dict(index.project_settings), index.model_settings_bytes
    stripped = {}
    if index.findings:
        print("WARNING: this project has post-processing scripts (post_process). Bambu Studio runs them as "
              "programs on this computer when it slices:")
        for f in index.findings:
            print("  in %s: %s" % (f.place(), bc.safe_text(f.value, 300) or "(see the file)"))
        if index.more_findings:
            print("  ... and %d more post_process findings" % index.more_findings)
        if a.strip_post_process:
            stripped = bc.strip_post_process(index)
            print("  --strip-post-process: removed from out.3mf.")
            if PS_NORM in stripped:
                ps = bc.settings_dict(stripped[PS_NORM], bc.PROJECT_SETTINGS)
            ms = stripped.get(MS_NORM, ms)
        else:
            print("  They are kept as they are. Do not slice this file unless you trust its author; "
                  "--strip-post-process removes them.")

    changes = []
    for k, v in plate.items():
        old = ps.get(k)
        v = bc.as_str(v)
        if isinstance(old, list) and not isinstance(v, list):
            v = [v] * max(1, len(old))
        if old != v:
            changes.append((k, old, v))
        ps[k] = v

    report = []
    ms2 = tune_objects(ms, rules, clears, report)
    tree2 = bc.xml_tree(ms2, "Metadata/model_settings.config")  # also proves the edited file is well-formed
    objs = bc.parse_objects(tree2)
    plate_of, _ = bc.parse_plates(tree2)
    print("Plate settings changed:")
    for k, old, v in changes:
        print("  %-28s %s -> %s" % (k, bc.safe_json(old, 120), bc.safe_json(v, 120)))
    if not changes:
        print("  (none)")
    print("\nObjects (plate | name | layer height | own settings | why):")
    for o in objs:
        r = next((x for x in report if x[0] == o["name"]), (o["name"], {}, ""))
        print("  %s | %-36s | %-5s | %s | %s" % (
            plate_of.get(o["id"], 1), bc.safe_text(o["name"], 36), bc.safe_text(bc.effective(o, ps, "layer_height"), 12),
            bc.safe_json(o["settings"], 80) if o["settings"] else "-", bc.safe_text(r[2], 120)))
    unmatched = [r["match"] for r in rules if not any(r["rx"].search(o["name"]) for o in objs)]
    for u in unmatched:
        print("WARNING: rule %r matched no object" % u)

    problems = bc.layer_height_check(objs, ps, plate_of)
    for pl, msg in problems:
        print("PROBLEM plate %s: %s" % (pl, msg))
    if problems and not a.force:
        sys.exit("Not written: give every object on a prime-tower plate one layer height (or turn the prime tower off "
                 "for a single-filament plate). --force writes anyway.")
    if a.dry_run:
        if index.unsafe:
            print("\nwould leave out unsafe entry names: %s" % ", ".join(bc.safe_text(n, 80) for n in index.unsafe))
        if os.path.lexists(a.dst) and not a.overwrite:
            print("\nnote: %s already exists; a real run needs --overwrite to replace it." % a.dst)
        print("\nDRY RUN - nothing written.")
        return

    had_gcode = any(stale_entry(n) for n in index.entries)
    replace = dict(stripped)
    replace[PS_NORM] = json.dumps(ps, indent=4)
    replace[MS_NORM] = strip_slice_refs(ms2) if had_gcode else ms2
    if had_gcode and MODEL_RELS in index.entries:
        replace[MODEL_RELS] = strip_gcode_rels(index.read(index.entries[MODEL_RELS], bc.MAX_SETTINGS_BYTES))
    if had_gcode and SLICE_INFO in index.entries and index.parsed[SLICE_INFO].fmt == "xml":
        replace[SLICE_INFO] = strip_slice_plates(replace.get(SLICE_INFO, index.parsed[SLICE_INFO].data))

    def writer(tmp):
        result = bc.write_project(index, tmp, replace, stale_entry)
        if a.strip_post_process:
            bc.check_written(tmp)
        return result

    unsafe, dropped = bc.atomic_write(a.dst, writer, a.overwrite)
    print("\nwrote %s%s" % (a.dst, ("  (dropped stale %s; cleared the slice results that pointed to them)"
                                    % ", ".join(bc.safe_text(n, 80) for n in dropped)) if dropped else ""))
    if unsafe:
        print("left out unsafe entry names: %s" % ", ".join(bc.safe_text(n, 80) for n in unsafe))


if __name__ == "__main__":
    bc.run_main(main)
