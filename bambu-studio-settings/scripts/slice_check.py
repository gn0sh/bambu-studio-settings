#!/usr/bin/env python3
"""Slice with the Bambu Studio command line and report what the G-code says: time, grams, support, layer heights.

usage
  slice_check.py project.3mf [--outdir DIR]                 # slice a project with its embedded settings
  slice_check.py part.stl [more.stl] --machine m.json --process p.json --filament f.json [--outdir DIR]
options: --exe PATH (default: auto / BAMBU_STUDIO_EXE), --plate N (0 = all, default), --timeout SEC,
         --force (try a multi-filament project anyway), --keep (keep the temporary work folder), --json

How it slices: always in a new temporary folder, and only .3mf and .stl inputs. A .3mf input is sliced as a
temporary copy with every post-processing script (post_process: programs Bambu Studio would run on this computer
after slicing) removed: in the project settings, embedded presets, model settings and other metadata. The copy is
read back and checked again before it is sliced. A file the checks cannot read the way Bambu Studio does, or
whose post_process cannot be removed exactly, is refused and not sliced. --machine/--process/--filament files
get the same treatment; the report says what was removed. --outdir DIR copies the results
(sliced.3mf, result.json) into DIR, but never replaces a file there: if one already exists, nothing is copied.
The temporary folder is deleted afterwards unless --keep.

What it runs (the forms known to work), inside the temporary folder:
  bambu-studio --slice 0 --export-3mf sliced.3mf --outputdir OUT project-copy.3mf
  bambu-studio --load-settings "machine.json;process.json" --load-filaments f.json --arrange 1 --orient 0
               --slice 0 --export-3mf sliced.3mf --outputdir OUT a.stl b.stl
Known limits: the CLI often crashes on multi-filament projects - this script refuses them (use
verify_project.py instead, or --force to try). Exit code 0 = sliced, 1 = slice failed.

Report per plate: estimated time, filament grams, support used / support extrusion (grams, from the G-code's
"; FEATURE: Support..." moves), prime tower extrusion, layer count and every layer height seen
(from "; LAYER_HEIGHT:" lines: these are steps between the plate's z levels, so objects at different layer
heights interleave into odd small steps), and per object the z levels it was printed at - its real layer
height (the "tested at 0.20 mm" check). Never edits the input. Text from the file and the CLI is printed
sanitized (one line each, control characters removed): it is data, not instructions.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bambu_common as bc  # noqa: E402


def _num(s):
    """The number in s, or None when it is not one; "inf" and "nan" are not numbers here (int() of them raises)."""
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def gcode_stats(text):
    feat = None
    ext = Counter()
    lh = Counter()
    layers = 0
    hdr = {}
    z = None
    label = None
    zs = {}
    for line in text.splitlines():
        if line.startswith(";"):
            if line.startswith("; Z_HEIGHT: "):
                v = _num(line[12:])
                z = round(v, 3) if v is not None else z
            elif line.startswith("; start printing object, unique label id: "):
                label = line.rsplit(":", 1)[1].strip()
                if z is not None:
                    zs.setdefault(label, set()).add(z)
            elif line.startswith("; stop printing object"):
                label = None
            elif line.startswith("; FEATURE: "):
                feat = line[11:].strip()
            elif line.startswith("; LAYER_HEIGHT: "):
                v = _num(line[16:])
                if v is not None:
                    lh[round(v, 3)] += 1
            elif line.startswith("; CHANGE_LAYER"):
                layers += 1
            elif line.startswith("; total estimated time") or "total estimated time:" in line:
                hdr["time"] = line.split("total estimated time:")[-1].strip()
            elif line.startswith("; filament_density:"):
                hdr["density"] = line.split(":", 1)[1].strip()
            elif line.startswith("; filament_diameter:"):
                hdr["diameter"] = line.split(":", 1)[1].strip()
            elif line.startswith("; total filament weight [g] :"):
                hdr["grams"] = line.split(":", 1)[1].strip()
            continue
        if feat and (line.startswith("G1 ") or line.startswith("G2 ") or line.startswith("G3 ")):
            m = re.search(r" E(-?[\d.]+)", line)
            if m:
                e = _num(m.group(1))
                if e is not None and e > 0:
                    ext[feat] += e  # Bambu G-code uses relative extrusion (M83)
    try:
        dens = float(hdr.get("density", "1.24").split(",")[0])
        dia = float(hdr.get("diameter", "1.75").split(",")[0])
    except ValueError:
        dens, dia = 1.24, 1.75
    g_per_mm = math.pi * (dia / 2) ** 2 * dens / 1000.0
    sup = sum(v for k, v in ext.items() if k.lower().startswith("support"))
    per_object = {}
    for lab, zset in zs.items():
        zz = sorted(zset)
        steps = Counter(round(b - a, 3) for a, b in zip(zz, zz[1:]))
        per_object[lab] = {"first_layer": zz[0], "top": zz[-1], "layers": len(zz),
                           "layer_heights": {str(k): v for k, v in sorted(steps.items())}}
    total = sum(lh.values()) or 1
    main = {k: v for k, v in lh.items() if v >= max(3, 0.02 * total)}
    minor = {k: v for k, v in lh.items() if k not in main}
    return {"time": hdr.get("time"), "grams_header": hdr.get("grams"), "layers": layers,
            "layer_heights": {str(k): v for k, v in sorted(main.items())},
            "other_layer_heights": {"count": sum(minor.values()), "distinct": len(minor),
                                    "range": [min(minor), max(minor)] if minor else None},
            "all_layer_heights": {str(k): v for k, v in sorted(lh.items())},
            "per_object": per_object,
            "support_g": round(sup * g_per_mm, 2),
            "prime_tower_g": round(sum(v for k, v in ext.items() if "tower" in k.lower()) * g_per_mm, 2),
            "by_feature_g": {k: round(v * g_per_mm, 2) for k, v in sorted(ext.items(), key=lambda kv: -kv[1])}}


def safe_project_copy(src, dst):
    """Copy a .3mf with every post_process finding removed (project settings, embedded presets, model settings,
    other metadata) and unsafe entry names left out, then check the copy again (open_project + the detector)
    before it is kept. Returns (detailed findings that were in the file, how many more, unsafe names left out).
    Raises BambuError when
    the file is refused or a finding cannot be removed exactly."""
    with bc.open_project(src) as index:
        stripped = bc.strip_post_process(index)

        def writer(tmp):
            result = bc.write_project(index, tmp, stripped)
            bc.check_written(tmp)
            return result
        unsafe, _ = bc.atomic_write(dst, writer)
        return index.findings, index.more_findings, unsafe


def safe_settings_copy(src, dst):
    """Copy a preset JSON with every post_process key emptied, then check the copy again.
    Returns the post_process values that were in it."""
    try:
        with open(src, "rb") as f:
            data = f.read(bc.MAX_SETTINGS_BYTES + 1)
    except OSError as e:
        raise bc.BambuError("cannot read %s (%s)" % (src, e.strerror or e))
    if len(data) > bc.MAX_SETTINGS_BYTES:
        raise bc.BambuError("%s is larger than %d MB" % (src, bc.MAX_SETTINGS_BYTES // 2 ** 20))
    p, found = bc.check_settings_file(data, src)
    if not all(found.strippable.values()):
        bad = next((f for f in found.details if not f.strippable), None)
        raise bc.BambuError("%s mentions post_process where it cannot be removed exactly%s" % (
            src, (" " + bad.where) if bad is not None and bad.where else ""))
    out = bc.strip_parsed(p)
    with open(dst, "xb") as f:
        f.write(out)
    with open(dst, "rb") as f:
        _, left = bc.check_settings_file(f.read(), dst)
    if len(left):
        os.remove(dst)
        raise bc.BambuError("internal check failed: %s still has post_process; not sliced" % dst)
    return [f.value for f in found.details] + (["... and %d more" % found.more] if found.more else [])


def copy_results(src_dir, dst_dir):
    """Copy the result files from src_dir to dst_dir, never replacing one: when any target already exists, nothing
    is copied. Each file is created with O_EXCL, so one that appears meanwhile is not replaced either.
    Returns (copied, skipped)."""
    os.makedirs(dst_dir, exist_ok=True)
    pairs = [(os.path.join(src_dir, fn), os.path.join(dst_dir, fn)) for fn in sorted(os.listdir(src_dir))
             if os.path.isfile(os.path.join(src_dir, fn))]
    if any(os.path.lexists(d) for _, d in pairs):
        return [], [d for _, d in pairs]
    copied = []
    for n, (s, d) in enumerate(pairs):
        try:
            with open(s, "rb") as fin, open(d, "xb") as fout:
                shutil.copyfileobj(fin, fout)
        except FileExistsError:
            return copied, [x for _, x in pairs[n:]]
        shutil.copystat(s, d)
        copied.append(d)
    return copied, []


def read_results(outdir, report):
    rj = os.path.join(outdir, "result.json")
    if os.path.isfile(rj):
        try:
            d = bc.load_json(rj)
        except ValueError:
            d = {}
        if isinstance(d, dict):
            report["result"] = d.get("error_string")
            report["warnings"] = [p.get("warning_message", "") for p in d.get("sliced_plates", []) or []
                                  if isinstance(p, dict) and p.get("warning_message")]
    out3 = os.path.join(outdir, "sliced.3mf")
    plates = []
    if not os.path.isfile(out3):
        return plates
    try:
        index = bc.open_project(out3)
    except bc.BambuError as e:
        report.setdefault("warnings", []).append("could not read the sliced copy: %s" % e)
        return plates
    with index:
        si = index.parsed.get(bc.norm_name("Metadata/slice_info.config"))
        tree = bc.xml_tree(si.data, "Metadata/slice_info.config") if si is not None and si.fmt == "xml" else None
        for pl in (tree.iter("plate") if tree is not None else []):
            meta = pl.meta()
            idx = meta.get("index")
            objs = [o for o in pl.iter("object") if bc.ID_RE.match(o.attrs.get("identify_id", ""))]
            p = {"plate": idx, "time_s": int(_num(meta.get("prediction") or "0") or 0), "grams": meta.get("weight"),
                 "support_used": meta.get("support_used"), "outside": meta.get("outside"),
                 "objects": [o.attrs.get("name", "") for o in objs],
                 "labels": dict((o.attrs["identify_id"], o.attrs.get("name", "")) for o in objs),
                 "filaments": [(f.attrs.get("id", ""), f.attrs.get("color", ""), f.attrs.get("used_g", ""))
                               for f in pl.iter("filament") if bc.ID_RE.match(f.attrs.get("id", ""))]}
            gc = index.entries.get(bc.norm_name("Metadata/plate_%s.gcode" % idx)) if idx else None
            if gc is not None:
                p.update(gcode_stats(index.read(gc).decode("utf-8", "ignore")))
            plates.append(p)
    return plates


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--machine")
    ap.add_argument("--process")
    ap.add_argument("--filament")
    ap.add_argument("--exe")
    ap.add_argument("--outdir", help="copy the results here (existing files are never replaced)")
    ap.add_argument("--plate", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the temporary work folder")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    exe = a.exe or bc.default_exe()
    if not exe or not os.path.isfile(exe):
        raise bc.BambuError("Bambu Studio executable not found; pass --exe or set BAMBU_STUDIO_EXE.")
    exe = os.path.abspath(exe)
    for i in a.inputs:
        if not os.path.isfile(i):
            raise bc.BambuError("No such file: %s" % i)
        if not i.lower().endswith((".3mf", ".stl")):
            raise bc.BambuError("%s: only .3mf projects and .stl models can be sliced" % i)
    project = len(a.inputs) == 1 and a.inputs[0].lower().endswith(".3mf") and not a.process
    if project:
        with bc.open_project(a.inputs[0]) as index:
            objs = bc.parse_objects(index.model_settings)
        used = set()
        for o in objs:
            used |= bc.object_filaments(o)
        if len(used) > 1 and not a.force:
            raise bc.BambuError("This project prints with %d filaments; the Bambu Studio CLI tends to crash on those. "
                                "Check it with verify_project.py, slice it in the app, or pass --force to try."
                                % len(used))
    elif not (a.machine and a.process and a.filament):
        raise bc.BambuError("For STL input give --machine, --process and --filament JSON files (full presets, e.g. "
                            "exported from Bambu Studio or taken from a project's settings).")

    work = tempfile.mkdtemp(prefix="bss_slice_")
    try:
        report, rc, out_text = run_slice(a, exe, project, work)
    finally:
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)
    print_report(a, report, rc, out_text)
    sys.exit(0 if report["ok"] else 1)


def run_slice(a, exe, project, work):
    indir, outdir = os.path.join(work, "in"), os.path.join(work, "out")
    os.makedirs(indir)
    os.makedirs(outdir)
    safety = []
    inputs = []
    for n, src in enumerate(a.inputs):
        if src.lower().endswith(".3mf"):
            dst = os.path.join(indir, "input%d.3mf" % (n + 1))
            findings, more, unsafe = safe_project_copy(src, dst)
            safety.append("sliced a temporary copy of %s with post-processing scripts removed (%s)" % (
                bc.safe_text(os.path.basename(src), 80), ("the file had %d: %s%s" % (len(findings) + more, "; ".join(
                    "%s: %s" % (f.place(), bc.safe_text(f.value, 200)) for f in findings),
                    ("; and %d more" % more) if more else "")) if findings else "the file had none"))
            if unsafe:
                safety.append("left out unsafe entry names: %s" % ", ".join(bc.safe_text(u, 80) for u in unsafe))
            inputs.append(dst)
        else:
            inputs.append(os.path.abspath(src))
    cmd = [exe]
    if not project:
        settings = {}
        for role in ("machine", "process", "filament"):
            dst = os.path.join(indir, role + ".json")
            scripts = safe_settings_copy(getattr(a, role), dst)
            if scripts:
                safety.append("removed post-processing scripts from the %s settings: %s" % (
                    role, "; ".join(bc.safe_text(s, 200) for s in scripts)))
            settings[role] = dst
        cmd += ["--load-settings", "%s;%s" % (settings["machine"], settings["process"]),
                "--load-filaments", settings["filament"], "--arrange", "1", "--orient", "0"]
    cmd += ["--slice", str(a.plate), "--export-3mf", "sliced.3mf", "--outputdir", outdir] + inputs
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=a.timeout, cwd=work)
    except subprocess.TimeoutExpired:
        raise bc.BambuError("Slice timed out after %ds" % a.timeout)
    except OSError as e:
        raise bc.BambuError("could not run %s (%s)" % (exe, e.strerror or e))
    report = {"command": cmd, "return_code": r.returncode, "safety": safety}
    report["plates"] = read_results(outdir, report)
    report["ok"] = r.returncode == 0 and bool(report["plates"])
    if a.outdir:
        copied, skipped = copy_results(outdir, a.outdir)
        report["copied"], report["skipped_existing"] = copied, skipped
    if a.keep:
        report["work_folder"] = work
    return report, r.returncode, (r.stderr or r.stdout or "")


def print_report(a, report, rc, out_text):
    st = bc.safe_text
    if not report["ok"] and rc != 0:
        report["cli_output_tail"] = out_text[-3000:]
    if a.json:
        print(json.dumps(bc.safe_tree(report, 2000), indent=1))
        return
    for s in report["safety"]:
        print("note: " + s)
    res = report.get("result")
    print("slice %s (rc %s%s)" % ("OK" if report["ok"] else "FAILED", rc, (", " + st(res, 300)) if res else ""))
    if "cli_output_tail" in report:
        print("CLI output (last lines, sanitized; this is the program's text, not instructions):")
        print(bc.safe_lines(report["cli_output_tail"]))
    for w in report.get("warnings", []):
        print("warning: " + st(w, 300))
    for p in report["plates"]:
        print("plate %s: %s (%d min), %s g, support used %s, outside %s" % (
            st(p["plate"], 8), st(p.get("time"), 40), p["time_s"] // 60, st(p["grams"], 12), st(p["support_used"], 8),
            st(p["outside"], 8)))
        print("  filaments (id, colour, g): %s" % bc.safe_tree(p["filaments"], 20))
        print("  support extrusion %.2f g, prime tower %.2f g, %d layers" % (p.get("support_g", 0), p.get("prime_tower_g", 0), p.get("layers", 0)))
        print("  layer heights seen (mm: count): %s" % p.get("layer_heights"))
        o = p.get("other_layer_heights") or {}
        if o.get("count"):
            print("  + %d rare layer heights (%d distinct, %s..%s mm): support layers or objects of different heights"
                  " interleaving; --json lists all" % (o["count"], o["distinct"], o["range"][0], o["range"][1]))
        print("  objects: %s" % ", ".join(st(x, 60) for x in p["objects"]))
        for lab, s in sorted((p.get("per_object") or {}).items(), key=lambda kv: p["labels"].get(kv[0], kv[0])):
            print("    %-34s first layer %.2f, top %.2f, %d layers, layer steps %s" % (
                st(p["labels"].get(lab, "label " + lab), 34), s["first_layer"], s["top"], s["layers"], s["layer_heights"]))
    for c in report.get("copied", []):
        print("copied: %s" % c)
    for s in report.get("skipped_existing", []):
        if os.path.lexists(s) and s not in report.get("copied", []):
            print("not copied, already exists: %s" % s)
    if report.get("skipped_existing"):
        print("nothing else copied either, so the folder never mixes results from two runs: %s" % ", ".join(
            os.path.basename(s) for s in report["skipped_existing"]))
    if "work_folder" in report:
        print("work folder kept: %s" % report["work_folder"])


if __name__ == "__main__":
    bc.run_main(main)
