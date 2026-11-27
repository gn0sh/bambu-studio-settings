#!/usr/bin/env python3
"""Create a Bambu Studio user preset (process, filament or machine): "<name>.json" (overrides only) + "<name>.info".

DRY RUN BY DEFAULT: prints both files and where they would go. Add --write to write them.
A new user preset is uploaded to the user's Bambu cloud account the next time Bambu Studio runs signed in,
and Bambu Studio only reads presets at startup (restart it to see the new one).

Examples
  make_preset.py --user-dir "<BambuStudio user folder>/user/<user_id>" --type process \
      --base "0.16mm Balanced Quality @BBL H2D" --name "Photo print H2D" \
      --set seam_position=back --set wall_loops=3 --set top_shell_layers=5
  make_preset.py --user-dir ... --type filament --base "Bambu PLA Basic @BBL X1C" --name "PLA Basic - 65C plate" \
      --overrides overrides.json --write

Rules this script enforces
  * the base must be a real system preset of that type in the install (list_presets.py shows exact names);
  * override keys must be known Bambu Studio settings (assets/known_setting_keys.txt + the install's presets;
    --allow-unknown-keys skips that check);
  * the name must be a valid file name on Windows, macOS and Linux (no reserved names such as CON or COM1,
    no trailing dot or space);
  * it refuses a name already used by a preset of that type in the user folder (any subfolder, compared
    case-insensitively, by preset name and by file name). --overwrite only replaces the exact
    "<type>/<name>.json" + ".info" pair this command would write (an existing preset is the user's own work:
    only overwrite when they asked for exactly that);
  * it does not add post-processing scripts (post_process).
Scalar values given for list settings are repeated for every entry (e.g. one per extruder variant).
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bambu_common as bc  # noqa: E402

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | \
    {"LPT%d" % i for i in range(1, 10)}
ID_KEY = {"process": "print_settings_id", "filament": "filament_settings_id", "machine": "printer_settings_id"}
# Keys Bambu Studio itself writes into user presets, copied from the resolved base when present.
COPY_FROM_BASE = {"process": ("print_extruder_id", "print_extruder_variant"),
                  "filament": ("filament_extruder_variant",),
                  "machine": ()}


def name_problem(name):
    """Why a preset name cannot be a file name everywhere, or None."""
    if not name.strip():
        return "is empty"
    if name != name.strip():
        return "starts or ends with a space"
    if name.endswith("."):
        return "ends with a dot"
    if re.search(r'[<>:"/\\|?*]', name) or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return "has characters that cannot be in a file name"
    if name.split(".")[0].strip().upper() in WINDOWS_RESERVED:
        return "is a reserved file name on Windows"
    if len(name) > 200:
        return "is longer than 200 characters"
    return None


def same_name_files(user_dir, ptype, name):
    """Preset files (.json/.info, any subfolder of user_dir/<ptype>) whose file name or "name" field equals name,
    compared case-insensitively."""
    want = name.casefold()
    hits = []
    for dp, _, files in os.walk(os.path.join(user_dir, ptype)):
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in (".json", ".info"):
                continue
            path = os.path.join(dp, f)
            pname = None
            if ext.lower() == ".json":
                try:
                    d = bc.load_json(path)
                    pname = d.get("name") if isinstance(d, dict) else None
                except Exception:
                    pass
            if stem.casefold() == want or (isinstance(pname, str) and pname.casefold() == want):
                hits.append(path)
    return sorted(hits)


def parse_value(s, where):
    s = s.strip()
    if s.startswith("["):
        try:
            return json.loads(s)
        except ValueError as e:
            raise bc.BambuError("%s: %s is not a valid JSON list (%s)" % (where, s, e))
    return s


def build(a, sp):
    base = sp.by_name.get(a.base)
    if base is None:
        close = [n for n in sp.by_name if all(w.lower() in n.lower() for w in a.base.split()[:3])][:10]
        sys.exit("Base preset %r is not in the install. Close names:\n  %s\nRun list_presets.py for exact names."
                 % (a.base, "\n  ".join(close) or "(none)"))
    if base["_type"] != a.type:
        sys.exit("Base %r is a %s preset, not %s." % (a.base, base["_type"], a.type))
    if str(base.get("instantiation", "")).lower() != "true":
        sys.exit("Base %r is an internal template (instantiation=false). Pick a preset shown in Bambu Studio." % a.base)
    if a.name in sp.by_name:
        sys.exit("Name %r is a system preset name; choose a different name." % a.name)
    why = name_problem(a.name)
    if why:
        sys.exit("Name %r %s; choose a different name." % (a.name, why))
    resolved = sp.resolve(a.base)

    overrides = {}
    if a.overrides:
        try:
            ov = bc.load_json(a.overrides)
        except OSError as e:
            raise bc.BambuError("cannot read %s (%s)" % (a.overrides, e.strerror or e))
        except ValueError as e:
            raise bc.BambuError("%s is not valid JSON (%s)" % (a.overrides, e))
        if not isinstance(ov, dict):
            raise bc.BambuError("%s must be a JSON object of {key: value}" % a.overrides)
        overrides.update(ov)
        gcode = sorted(k for k in ov if isinstance(k, str) and k.endswith("_gcode"))
        if gcode:
            print("note: %s sets custom G-code (it runs on the printer): %s - read it before printing" % (
                a.overrides, ", ".join(bc.safe_text(k, 60) for k in gcode)))
    for kv in a.set or []:
        if "=" not in kv:
            sys.exit("--set wants key=value, got %r" % kv)
        k, v = kv.split("=", 1)
        overrides[k.strip()] = parse_value(v, "--set %s" % k.strip())

    problems, out = [], {}
    known = bc.known_keys(sp) | set(resolved)
    for k, v in overrides.items():
        if not bc.SETTING_KEY_RE.match(k):
            problems.append("%r is not a valid setting key (letters, digits and _ only)" % k)
            continue
        if bc.post_process_scripts({k: v}):
            problems.append("post_process (post-processing scripts) is not set by this script; add it in Bambu Studio "
                            "yourself if you really want it")
            continue
        if k in ("name", "inherits", "from", ID_KEY[a.type], "version"):
            problems.append("%s is set by this script; leave it out" % k)
            continue
        if k not in known and not a.allow_unknown_keys:
            problems.append("%s is not a known Bambu Studio setting (typo? or pass --allow-unknown-keys)" % k)
            continue
        if k not in resolved:
            print("note: %s is not in the base preset file; Bambu Studio's built-in default applies today" % k)
        old = resolved.get(k)
        v = bc.as_str(v)
        if isinstance(old, list) and not isinstance(v, list):
            v = [v] * max(1, len(old))
        if v == old:
            print("note: %s is already %r in the base; not stored" % (k, v))
            continue
        out[k] = v
    if problems:
        sys.exit("Refusing:\n  " + "\n  ".join(problems))

    preset = {"from": "User", "inherits": a.base, "name": a.name}
    for k in COPY_FROM_BASE[a.type]:
        if k in resolved:
            preset[k] = resolved[k]
    preset[ID_KEY[a.type]] = [a.name] if a.type == "filament" else a.name
    preset["version"] = bc.version_dotted(sp.version)
    preset.update(out)
    preset = dict(sorted(preset.items()))  # Bambu Studio writes keys sorted

    user_id = os.path.basename(os.path.normpath(a.user_dir))
    info = ("sync_info = %s\nuser_id = %s\nsetting_id = \nbase_id = %s\nupdated_time = %d\n"
            % (a.sync_info, "" if user_id == "default" else user_id, base.get("setting_id", ""), int(time.time())))
    return preset, info, out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-dir", required=True, help="<BambuStudio user folder>/user/<user_id> (or a scratch copy)")
    ap.add_argument("--type", required=True, choices=bc.PRESET_TYPES)
    ap.add_argument("--base", required=True, help="exact system preset name to inherit from")
    ap.add_argument("--name", required=True, help="name of the new preset (also its file name)")
    ap.add_argument("--set", action="append", metavar="KEY=VALUE", help="an override; repeatable. JSON list allowed")
    ap.add_argument("--overrides", help="JSON file of {key: value} overrides")
    ap.add_argument("--profiles", help="install's resources/profiles folder (auto-detected if omitted)")
    ap.add_argument("--vendor", default="BBL")
    ap.add_argument("--sync-info", default="create",
                    help="value for sync_info in .info (default 'create' = a new local preset waiting to upload)")
    ap.add_argument("--allow-unknown-keys", action="store_true")
    ap.add_argument("--overwrite", action="store_true", help="replace an existing preset of the same name")
    ap.add_argument("--write", action="store_true", help="actually write the files (default: dry run)")
    a = ap.parse_args()

    if not os.path.isdir(a.user_dir):
        sys.exit("No such folder: %s" % a.user_dir)
    if os.path.basename(os.path.dirname(os.path.normpath(a.user_dir))) != "user":
        print("warning: %s is not of the form .../user/<user_id>; Bambu Studio will not see presets here" % a.user_dir)
    sp = bc.SystemPresets(bc.find_profiles(a.profiles), a.vendor)
    preset, info, out = build(a, sp)

    folder = os.path.join(a.user_dir, a.type)
    jp, ip = os.path.join(folder, a.name + ".json"), os.path.join(folder, a.name + ".info")
    print("=== %s\n%s\n=== %s\n%s" % (jp, json.dumps(preset, indent=4), ip, info))
    print("overrides stored: %d (%s)" % (len(out), ", ".join(out) or "none"))
    mine = {os.path.normcase(os.path.abspath(jp)), os.path.normcase(os.path.abspath(ip))}
    hits = same_name_files(a.user_dir, a.type, a.name)
    others = [h for h in hits if os.path.normcase(os.path.abspath(h)) not in mine]
    if others:
        sys.exit("A %s preset named %r (ignoring case) already exists: %s. Not touching it; choose another name "
                 "(--overwrite only replaces %s)." % (a.type, a.name, ", ".join(bc.safe_text(h, 200) for h in others), jp))
    exists = os.path.lexists(jp) or os.path.lexists(ip)
    if exists and not a.overwrite:
        sys.exit("A preset named %r already exists there. Not touching it (add --overwrite only if the user asked)." % a.name)
    if not a.write:
        print("DRY RUN - nothing written. Re-run with --write to create it.")
        return
    os.makedirs(folder, exist_ok=True)
    mode = "w" if a.overwrite else "x"  # "x": fail rather than replace a file that appeared meanwhile
    try:
        with open(jp, mode, encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(preset, indent=4) + "\n")
        try:
            with open(ip, mode, encoding="utf-8", newline="\n") as f:
                f.write(info)
        except FileExistsError:
            if not a.overwrite:
                os.remove(jp)  # the .json was just created by this run; do not leave half a preset
            raise
    except FileExistsError as e:
        raise bc.BambuError("%s appeared while writing; not replacing it." % e.filename)
    print("WROTE %s and .info. Restart Bambu Studio to see it; it will sync to the signed-in Bambu account." % jp)


if __name__ == "__main__":
    bc.run_main(main)
