#!/usr/bin/env python3
"""List Bambu Studio presets: the system presets each printer can use (read from the install), and user presets.

Never guess a system preset name - names differ per printer (a P1S uses the "@BBL X1C" process presets; an H2D
has "0.16mm Balanced Quality @BBL H2D", not "0.16mm Optimal"). Run this and copy the exact name.

Examples
  list_presets.py --printers                                  # machine presets (printer + nozzle) in the install
  list_presets.py --printer "H2D 0.4"                         # process presets for matching printers
  list_presets.py --printer "P1S 0.4" --type filament --grep PLA
  list_presets.py --user                                       # user presets in every user/<user_id> folder
  list_presets.py --user --user-root <BambuStudio user folder> --json

Options: --profiles <install>/resources/profiles (auto-detected on Windows/macOS; or BAMBU_STUDIO_PROFILES),
         --vendor BBL (default).  Read-only: this script never writes anything.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bambu_common as bc  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profiles", help="install's resources/profiles folder (auto-detected if omitted)")
    ap.add_argument("--vendor", default="BBL")
    ap.add_argument("--printers", action="store_true", help="list machine presets (printer + nozzle) only")
    ap.add_argument("--printer", help="substring of the machine preset name, e.g. 'H2D 0.4' or 'P1S'")
    ap.add_argument("--type", choices=("process", "filament"), default="process")
    ap.add_argument("--grep", help="only presets whose name contains this text (case-insensitive)")
    ap.add_argument("--user", action="store_true", help="list user presets instead of system presets")
    ap.add_argument("--user-root", help="<BambuStudio user folder> (default: the platform's standard location)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    if a.user:
        return list_user(a)

    sp = bc.SystemPresets(bc.find_profiles(a.profiles), a.vendor)
    machines = sp.instantiated("machine")
    if a.printer:
        machines = [m for m in machines if all(w.lower() in m.lower() for w in a.printer.split())]
        if not machines:
            sys.exit("No machine preset matches %r. Try --printers." % a.printer)
    out = {"profiles_version": sp.version, "printers": []}
    for m in machines:
        r = sp.resolve(m)
        entry = {"machine": m, "printer_model": r.get("printer_model"), "nozzle": r.get("nozzle_diameter"),
                 "default_process": r.get("default_print_profile"),
                 "printable_area": r.get("printable_area"), "printable_height": r.get("printable_height"),
                 "extruder_printable_area": r.get("extruder_printable_area") or None}
        if not a.printers:
            names = sp.compatible(a.type, m)
            if a.grep:
                names = [n for n in names if a.grep.lower() in n.lower()]
            entry[a.type] = [{"name": n, "setting_id": sp.by_name[n].get("setting_id"),
                              "layer_height": sp.resolve(n).get("layer_height")} for n in names]
        out["printers"].append(entry)
    if a.json:
        print(json.dumps(out, indent=1))
        return
    print("Bambu Studio profiles %s (%s)" % (out["profiles_version"], a.vendor))
    for e in out["printers"]:
        print("\n%s  [model %s, nozzle %s]" % (e["machine"], e["printer_model"], ",".join(e["nozzle"] or [])))
        print("  bed %s, height %s%s" % (e["printable_area"], e["printable_height"],
              ("; per-nozzle reach %s" % e["extruder_printable_area"]) if e["extruder_printable_area"] else ""))
        print("  default process: %s" % e["default_process"])
        for p in e.get(a.type, []):
            lh = (" (layer %s)" % p["layer_height"]) if a.type == "process" else ""
            print("   - %s%s  [%s]" % (p["name"], lh, p["setting_id"]))


def list_user(a):
    root = a.user_root or bc.default_user_root()
    dirs = bc.user_dirs(root)
    if not dirs:
        sys.exit("No user/<user_id> folders under %s" % root)
    out = []
    for d in dirs:
        for t in bc.PRESET_TYPES:
            for dp, _, files in os.walk(os.path.join(d, t)):
                for f in sorted(files):
                    if not f.endswith(".json"):
                        continue
                    try:
                        j = bc.load_json(os.path.join(dp, f))
                    except Exception as ex:
                        out.append({"type": t, "file": f, "error": str(ex)})
                        continue
                    if not isinstance(j, dict):
                        out.append({"type": t, "file": f, "error": "not a JSON object"})
                        continue
                    if a.grep and a.grep.lower() not in str(j.get("name", f)).lower():
                        continue
                    info = {}
                    ip = os.path.join(dp, f[:-5] + ".info")
                    if os.path.isfile(ip):
                        with open(ip, encoding="utf-8", errors="replace") as fh:
                            for line in fh:
                                if "=" in line:
                                    k, v = line.split("=", 1)
                                    info[k.strip()] = v.strip()
                    keys = sorted(k for k in j if k not in ("name", "inherits", "from", "version", "print_settings_id",
                                  "filament_settings_id", "printer_settings_id", "print_extruder_id",
                                  "print_extruder_variant", "filament_extruder_variant"))
                    out.append({"type": t, "user_folder": os.path.basename(d), "subfolder": os.path.relpath(dp, d),
                                "name": j.get("name"), "inherits": j.get("inherits", "(none - a custom base)"),
                                "overrides": keys, "synced": bool(info.get("setting_id")),
                                "sync_info": info.get("sync_info", "")})
    out = bc.safe_tree(out, 300)  # names come from preset files: print them as one-line data
    if a.json:
        print(json.dumps(out, indent=1))
        return
    for e in out:
        if "error" in e:
            print("%-8s %s  UNREADABLE: %s" % (e["type"], e["file"], e["error"]))
            continue
        print("%-8s %-55s <- %s\n         overrides: %s%s" % (e["type"], e["name"], e["inherits"], ", ".join(e["overrides"]) or "-",
              "" if e["synced"] else "   (not yet synced: no cloud setting_id)"))


if __name__ == "__main__":
    bc.run_main(main)
