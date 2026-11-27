"""Mesh loading for the print-object-analyzer scripts: STL (binary/ASCII) and Bambu / 3MF projects.

A .3mf is read only through the bambu-studio-settings skill's hardened reader (bambu_common.open_project and
Geometry in triangles mode; see that skill's ADR 0006): zip container checks, size and ratio limits, XML without
DTDs or entities, and limits on component cycles, depth, uses, vertices and triangles. An object is read in its
placed orientation: the build item transform times every component transform (Bambu projects keep each mesh in
3D/Objects/object_N.model and point to it from 3D/3dmodel.model). Names, parts (normal / modifier / support
blocker...), filament slots, plates and each object's own setting overrides come from
Metadata/model_settings.config when the file has one.

STL files are read here, standalone, with a size limit and a triangle budget.
Everything read from a file is data from its author: print it only through poa_common.safe_text.
"""
import os
import re
import struct

import numpy as np

import poa_common as pc

# project settings the recommender needs (a subset - the full file is large and not needed)
PROJECT_KEYS = ["printer_model", "printer_settings_id", "print_settings_id", "nozzle_diameter", "layer_height",
                "initial_layer_print_height", "enable_prime_tower", "filament_colour", "filament_type",
                "line_width", "outer_wall_line_width", "inner_wall_line_width", "wall_loops", "top_shell_layers",
                "bottom_shell_layers", "top_shell_thickness", "sparse_infill_density", "sparse_infill_pattern",
                "seam_position", "seam_slope_type", "brim_type", "brim_width", "enable_support", "support_type",
                "support_threshold_angle", "support_on_build_plate_only", "outer_wall_speed", "enable_overhang_speed",
                "bridge_speed", "thick_bridges", "wall_generator", "detect_thin_wall", "elefant_foot_compensation",
                "precise_z_height", "slow_down_layer_time", "print_sequence", "curr_bed_type"]

MAX_TRIANGLES = 4000000              # triangles in all inputs together (about 4-5 minutes of analysis)
MAX_STL_BYTES = 512 * 1024 * 1024    # one STL file
MAX_BUILD_ITEMS = 2000               # objects (build items) in one project
SETTING_KEY = re.compile(r"^[A-Za-z0-9_]+\Z")  # \Z, not $: "key\n" is not a key
ASCII_CHUNK = 1 << 20                # ASCII STL bytes parsed at a time
STL_RECORD = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
_ASCII_VERTEX = re.compile(rb"vertex[ \t]+(\S+)[ \t]+(\S+)[ \t]+(\S+)")


class Budget(object):
    """Triangles left for this run, shared by every input."""

    def __init__(self, limit=MAX_TRIANGLES):
        self.limit, self.used = limit, 0

    def take(self, n, label):
        self.used += n
        if self.used > self.limit:
            raise pc.UserError("%s: the inputs have more than %d triangles (--max-triangles); simplify the mesh or "
                               "analyze fewer parts at a time" % (label, self.limit))


def load_stl(path, budget=None):
    """Triangles (n, 3, 3) of an STL file. Binary when the header's triangle count fits the file (trailing bytes
    after the last record are ignored), else ASCII ('solid ... vertex x y z'). Refuses (UserError): files over
    MAX_STL_BYTES, a binary file shorter than its header says, ASCII numbers that are not numbers, vertex counts
    that are not a multiple of 3, and non-finite coordinates."""
    budget = budget or Budget()
    label = pc.safe_text(os.path.basename(path), 80)
    size = os.path.getsize(path)
    if size > MAX_STL_BYTES:
        raise pc.UserError("%s is larger than %d MB" % (label, MAX_STL_BYTES // 2 ** 20))
    with open(path, "rb") as f:
        data = f.read(size + 1)  # A3: read(n) sets aside n bytes first, so not MAX_STL_BYTES for every file
    if len(data) > size:  # it grew since getsize
        raise pc.UserError("%s changed while it was read" % label)
    head = data[:512].lstrip()
    ascii_like = head[:5].lower() == b"solid" and b"facet" in data[:4096].lower()
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        need = 84 + 50 * n
        if need == len(data) or (need < len(data) and not ascii_like and n > 0):
            budget.take(n, label)
            T = np.frombuffer(data, dtype=STL_RECORD, count=n, offset=84)["v"].astype(np.float64)
            return _finite(T, label)
        if not ascii_like:
            if need > len(data):
                raise pc.UserError("%s looks like a binary STL cut short: its header says %d triangles (%d bytes) "
                                   "but the file has %d bytes" % (label, n, need, len(data)))
            raise pc.UserError("%s is neither a binary STL nor an ASCII one ('solid ... facet ... vertex')" % label)
    elif not ascii_like:
        raise pc.UserError("%s is too short to be an STL file" % label)
    return _finite(_ascii_vertices(data, label, budget).reshape(-1, 3, 3), label)


def _ascii_vertices(data, label, budget):
    """(n, 3) float64 vertices of an ASCII STL, parsed ASCII_CHUNK bytes at a time into one array sized from the
    'vertex' count (A3: a list of match tuples for every vertex took about 25 times the file). The budget is
    charged before the array is made, so a file with too many vertices is refused before it is parsed."""
    cap = 3 * (budget.limit - budget.used)
    most = data.count(b"vertex")  # at least the vertex lines (it also counts 'vertex' in a name: never fewer)
    if most > cap + 64:  # far past the budget: refused before any parsing (64: room for 'vertex' in names)
        budget.take(most // 3, label)
    out, n, i = np.empty((min(most, cap + 64), 3), np.float64), 0, 0
    while i < len(data):
        j = data.find(b"vertex", i + ASCII_CHUNK)  # cut just before a vertex, so no match is split
        j = len(data) if j < 0 else j
        vals = _ASCII_VERTEX.findall(data, i, j)
        i = j
        if not vals:
            continue
        if n + len(vals) > len(out):
            budget.take((n + len(vals)) // 3 + 1, label)  # past the budget: raises
        try:
            out[n:n + len(vals)] = np.array(vals, dtype=np.float64)
        except ValueError as e:
            raise pc.UserError("%s: ASCII STL has a vertex that is not three numbers (%s)" % (
                label, pc.safe_text(str(e), 80)))
        n += len(vals)
    if n % 3:
        raise pc.UserError("%s: ASCII STL with %d vertices, not a multiple of 3" % (label, n))
    budget.take(n // 3, label)
    return out[:n]


def _finite(T, label):
    if len(T) and not np.isfinite(T).all():
        raise pc.UserError("%s has coordinates that are not finite numbers (NaN or infinity)" % label)
    return T


def place(M, vs, ts):
    """Triangles in world space from bambu_common's flat vertex and index arrays and a 3MF transform (12 numbers,
    x' = m0 x + m3 y + m6 z + m9 ...)."""
    V = np.frombuffer(vs, dtype=np.float64).reshape(-1, 3)
    F = np.frombuffer(ts, dtype=np.dtype(ts.typecode)).reshape(-1, 3)
    R = np.array(M[:9], dtype=np.float64).reshape(3, 3)
    return (V @ R + np.array(M[9:12], dtype=np.float64))[F]


def object_settings(settings):
    """An object's own setting overrides, without post-processing scripts, G-code or odd keys (L4): those are not
    print settings the analyzer reasons about, and they should not travel into analysis.json."""
    out = {}
    for k, v in (settings or {}).items():
        lk = k.lower()
        if not SETTING_KEY.match(k) or lk == "post_process" or "gcode" in lk:
            continue
        out[k] = v
    return out


def load_3mf(path, bss_dir=None, budget=None):
    """-> (objects, project). Objects whose geometry cannot be placed safely carry an "error" instead of parts."""
    bc = pc.load_bss(bss_dir)
    budget = budget or Budget()
    label = pc.safe_text(os.path.basename(path), 80)
    try:
        index = bc.open_project(path)
    except bc.RefusedArchive as e:
        raise pc.UserError("refused: %s" % e)
    with index:
        main = bc.norm_name(bc.MAIN_MODEL)
        if main not in index.models:
            raise pc.UserError("%s is not a 3MF project (no 3D/3dmodel.model)" % label)
        geo = bc.Geometry(triangles=True, max_triangles=budget.limit - budget.used)
        try:
            for n, info in index.models.items():
                geo.add(n, index.read(info))
        except bc.GeometryError as e:
            raise pc.UserError("%s: geometry not read: %s" % (label, e))
        full = index.project_settings or {}
        ps = {k: full[k] for k in PROJECT_KEYS if k in full}
        info_of = {o["id"]: o for o in bc.parse_objects(index.model_settings)}
        plate_of, _ = bc.parse_plates(index.model_settings)
        project = {"file": os.path.basename(path), "settings": ps,
                   "post_process_findings": len(index.findings) + index.more_findings}
        objects = []
        items = geo.items.get(main, [])
        if len(items) > MAX_BUILD_ITEMS:
            raise pc.UserError("%s has %d objects on its plates; the analyzer reads at most %d" % (
                label, len(items), MAX_BUILD_ITEMS))
        for at in items:
            oid = at.get("objectid", "")
            if not bc.ID_RE.match(oid):
                objects.append(_error_object("build item %d" % (len(objects) + 1), None, 1, "a build item has no "
                                             "object id"))
                continue
            meta = info_of.get(oid, {"name": "", "extruder": None, "settings": {}, "parts": []})
            name = meta.get("name") or geo.names.get((main, oid)) or "object %s" % oid
            plate = plate_of.get(oid, 1)
            try:
                placed = geo.placed_meshes(main, oid, bc.tf(at.get("transform")))
            except bc.GeometryError as e:
                objects.append(_error_object(name, oid, plate, "geometry not read: %s" % e))
                continue
            pinfo = {p["id"]: p for p in meta.get("parts", [])}
            parts = []
            for fn, pid, M, vs, ts in placed:
                p = pinfo.get(pid, {})
                parts.append({"name": p.get("name") or name, "subtype": p.get("subtype") or "normal_part",
                              "tris": place(M, vs, ts)})
            budget.take(sum(len(p["tris"]) for p in parts), label)
            objects.append({"name": name, "id": oid, "plate": plate, "printable": at.get("printable", "1") != "0",
                            "filaments": sorted(bc.object_filaments(meta)),
                            "settings": object_settings(meta.get("settings")), "parts": parts})
    return objects, project


def _error_object(name, oid, plate, error):
    return {"name": name, "id": oid, "plate": plate, "printable": True, "filaments": [1], "settings": {},
            "parts": [], "error": error}


def load_inputs(paths, bss_dir=None, max_triangles=MAX_TRIANGLES):
    """-> (objects, project) where objects = [{name, id, plate, filaments, settings, parts:[{name,subtype,tris}]}]
    (plus "error" for an object that could not be read)."""
    objects, project = [], None
    budget = Budget(max_triangles)
    if any(p.lower().endswith(".3mf") for p in paths) and len(paths) > 1:
        raise pc.UserError("give one .3mf at a time (or several STL files)")
    for path in paths:
        if not os.path.isfile(path):
            raise pc.UserError("no such file: %s" % path)
        if path.lower().endswith(".3mf"):
            objects, project = load_3mf(path, bss_dir, budget)
        else:
            T = load_stl(path, budget)
            name = os.path.basename(path)
            objects.append({"name": name, "id": None, "plate": 1, "printable": True, "filaments": [1], "settings": {},
                            "parts": [{"name": name, "subtype": "normal_part", "tris": T}]})
    return objects, project
