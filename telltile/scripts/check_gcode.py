"""Check that every tile feature is really in the sliced G-code, at the height it was designed to.
Usage: python3 check_gcode.py sliced.3mf|plate_1.gcode features.txt [--json]
features.txt = OpenSCAD's ECHO output of tile.scad (lines "FEAT;name;x;y;z;r;kind"), model coordinates.
The model-to-bed offset is taken from the center of the first layer's extrusions (the base plate; size from the TILE echo line).
kinds: top    = highest extrusion within r of (x, y) must be z +-0.25 (one layer)
       tip    = as top, but the tip may stop up to 0.8 mm short (a cone point thinner than a line is dropped)
       bridge = there must be a "Bridge" extrusion within r of (x, y) at z +-0.25
       info   = as top, reported but never fails (a size test that is allowed to drop out, e.g. 1.5 mm text)
Exit 1 if any feature fails, 2 if an input is malformed or past a size cap (refused, never read slowly)."""
import sys, re, json, math, os, struct, zipfile, zlib, argparse, unicodedata

W, D = 112.0, 84.0                     # the tile's footprint; the TILE echo line overrides it

# Caps: a sliced tile is a few MB of G-code and about half a million sample points. Input past a cap is refused.
# The G-code cap is kept >= 2x the tile's G-code. Estimate (not yet measured on a real slice): 16.9 cm3 of tile at
# 0.42 x 0.20 mm lines is ~200 m of extrusion, ~6 MB of G1 lines even fully solid at 1 mm segments.
MAX_GCODE_BYTES = 16 * 1024 * 1024     # uncompressed G-code, plain or inside a 3mf (63 MB took 14.5 s)
MAX_3MF_BYTES = 64 * 1024 * 1024       # a sliced tile's .gcode.3mf is about 3.5 MB
MAX_3MF_ENTRIES = 10000
MAX_CD_BYTES = 1024 * 1024             # the zip central directory (a real 3mf's is a few KB)
CD_ENTRY_BYTES = 46 + 1024             # most one directory entry may take: fixed header + name, extra and comment
MAX_RATIO = 200                        # uncompressed / compressed size of the G-code entry (G-code deflates ~4:1)
MAX_LINE_BYTES = 1024 * 1024
MAX_GCODE_LINES = 2000000              # the real P1S tile slice has 129 804; 16 MB of blank lines was 16 M
CHUNK = 1024 * 1024
MAX_SEGMENT_MM = 400.0                 # longer than any bed's diagonal
MAX_COORD_MM = 1000.0
MAX_POINTS = 1000000                   # the real P1S tile slice has 507 K
MAX_FEATURES_BYTES = 1024 * 1024
MAX_FEATURES = 500
MAX_RADIUS_MM = 50.0
MAX_NEAR_CHECKS = 5000000              # point-distance checks for all features together (real slice: < 0.2 M)
KINDS = ("top", "tip", "bridge", "info")


class InputError(Exception):
    """A malformed input or one past a cap; main() prints it and exits 2."""


def safe_text(s, limit=80):
    t = "".join("?" if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") else ch for ch in str(s))
    return t if len(t) <= limit else t[:limit - 3] + "..."


def _lines(stream, label):
    """Decoded lines of a binary stream, read in chunks; refuses past MAX_GCODE_BYTES, a line past MAX_LINE_BYTES, or
    past MAX_GCODE_LINES lines (counted per chunk, before any line is handed on)."""
    total, buf, count = 0, b"", 0
    while True:
        chunk = stream.read(CHUNK)
        total += len(chunk)
        if total > MAX_GCODE_BYTES:
            raise InputError("%s: G-code over %d MB; a sliced tile is a few MB" % (label, MAX_GCODE_BYTES // 2 ** 20))
        if not chunk:
            break
        parts = (buf + chunk).replace(b"\r", b"\n").split(b"\n")
        buf = parts.pop()
        if len(buf) > MAX_LINE_BYTES:
            raise InputError("%s: a line over %d KB" % (label, MAX_LINE_BYTES // 1024))
        count += len(parts)
        if count > MAX_GCODE_LINES:
            raise InputError("%s: over %d lines; a sliced tile has about 130 000" % (label, MAX_GCODE_LINES))
        for line in parts:
            yield line.decode("utf-8", "ignore")
    if buf:
        yield buf.decode("utf-8", "ignore")


def check_zip_directory(path):
    """Read the end-of-central-directory record and refuse an archive whose central directory is over MAX_CD_BYTES
    or declares over MAX_3MF_ENTRIES entries, before zipfile.ZipFile parses the directory. Finds the record the way
    zipfile does: the last 22 bytes, else the last signature in the 64 KB comment window. Any zip64 locator in
    front of it is refused: a sliced tile never needs zip64, and zipfile would take the directory size and count
    from a zip64 record wherever the locator points."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - 22 - 65535))
        tail = fh.read()
        base = size - len(tail)
        i = len(tail) - 22 if tail[-22:-18] == b"PK\x05\x06" and tail[-2:] == b"\0\0" else tail.rfind(b"PK\x05\x06")
        if i < 0 or len(tail) - i < 22:
            raise InputError("%s: not a readable 3mf (no zip end record)" % path)
        _, _, _, n_disk, n_total, cd_size, _, _ = struct.unpack("<4s4H2LH", tail[i:i + 22])
        entries = max(n_disk, n_total)
        loc = base + i - 20                                    # where zipfile looks for a zip64 locator
        if loc >= 0:
            fh.seek(loc)
            if fh.read(4) == b"PK\x06\x07":
                raise InputError("%s: a zip64 archive; a sliced tile's 3mf is small and never zip64" % path)
    if cd_size > MAX_CD_BYTES:
        raise InputError("%s: zip directory of %d MB (cap %d MB; a 3mf's is a few KB)" % (path, cd_size // 2 ** 20,
                                                                                        MAX_CD_BYTES // 2 ** 20))
    if entries > MAX_3MF_ENTRIES:
        raise InputError("%s: %d entries (a 3mf has a few dozen)" % (path, entries))
    if cd_size > max(entries, 1) * CD_ENTRY_BYTES:      # the declared count is not trusted: it must fit the size
        raise InputError("%s: zip directory of %d bytes for %d declared entries (over %d bytes each)"
                         % (path, cd_size, entries, CD_ENTRY_BYTES))


def read_gcode(path):
    """Lines of the G-code in a .gcode file or the first .gcode entry of a sliced .3mf (read in memory, never
    extracted). File size, directory size, entry count and zip64 are checked before zipfile reads the directory;
    entry size, compression method and ratio before any byte is decompressed."""
    if not path.lower().endswith(".3mf"):
        if os.path.getsize(path) > MAX_GCODE_BYTES:
            raise InputError("%s: over %d MB; a sliced tile is a few MB" % (path, MAX_GCODE_BYTES // 2 ** 20))
        with open(path, "rb") as fh:
            for line in _lines(fh, path):
                yield line
        return
    if os.path.getsize(path) > MAX_3MF_BYTES:
        raise InputError("%s: over %d MB" % (path, MAX_3MF_BYTES // 2 ** 20))
    check_zip_directory(path)
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError) as e:     # ValueError: e.g. a UTF-8-flagged name that is not
        raise InputError("%s: not a readable 3mf (%s)" % (path, safe_text(e, 120))) from None
    with z:
        infos = z.infolist()
        if len(infos) > MAX_3MF_ENTRIES:
            raise InputError("%s: %d entries (a 3mf has a few dozen)" % (path, len(infos)))
        g = [i for i in infos if i.filename.lower().endswith(".gcode")]
        if not g:
            raise InputError("%s: no .gcode inside - slice it and export the sliced file (.gcode.3mf)" % path)
        i = g[0]
        if i.file_size > MAX_GCODE_BYTES:
            raise InputError("%s: %s is %d MB uncompressed (cap %d MB)" % (path, safe_text(i.filename), i.file_size // 2 ** 20,
                                                                          MAX_GCODE_BYTES // 2 ** 20))
        if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            # bzip2 and LZMA readers decompress without an output bound (927 bytes became 1 GB on Python 3.8)
            raise InputError("%s: %s uses compression method %d; a 3mf is stored or deflated"
                             % (path, safe_text(i.filename), i.compress_type))
        if i.file_size and (i.compress_size <= 0 or i.file_size / i.compress_size > MAX_RATIO):
            raise InputError("%s: %s compresses more than %d:1; G-code does not" % (path, safe_text(i.filename), MAX_RATIO))
        try:
            with z.open(i) as fh:
                for line in _lines(fh, path):
                    yield line
        except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError, OSError, ValueError) as e:
            raise InputError("%s: cannot read %s (%s)" % (path, safe_text(i.filename), safe_text(e, 120))) from None


def _num(text, where):
    try:
        v = float(text)
    except ValueError:
        raise InputError("%s: %r is not a number" % (where, safe_text(text, 40))) from None
    if not math.isfinite(v):
        raise InputError("%s: %r is not a finite number" % (where, safe_text(text, 40)))
    return v


def _arc(x, y, nx, ny, v, cw, where):
    """(center x, center y, start radius, end radius, start angle, signed sweep) of a G2 (cw) / G3 (ccw) move.
    I/J are the center relative to the start; R-form: R > 0 the short way round, R < 0 the long way."""
    if "I" in v or "J" in v:
        cx, cy = x + v.get("I", 0.0), y + v.get("J", 0.0)
    elif "R" in v:
        r, d = v["R"], math.hypot(nx - x, ny - y)
        if d == 0 or abs(r) < d / 2 - 1e-6:
            raise InputError("%s: an R arc whose radius cannot reach its end point" % where)
        h = math.sqrt(max(0.0, r * r - d * d / 4))
        side = (-1 if cw else 1) * (1 if r > 0 else -1)          # left of the chord is +
        cx = (x + nx) / 2 - side * h * (ny - y) / d
        cy = (y + ny) / 2 + side * h * (nx - x) / d
    else:
        raise InputError("%s: G2/G3 needs I/J or R" % where)
    a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
    sweep = ((a0 - a1) if cw else (a1 - a0)) % (2 * math.pi)
    if sweep < 1e-9:
        sweep = 2 * math.pi                                      # start == end: a full circle
    return cx, cy, math.hypot(x - cx, y - cy), math.hypot(nx - cx, ny - cy), a0, -sweep if cw else sweep


def samples(lines):
    """(x, y, z, feature) points along every extruding move, 0.4 mm apart (arcs along the arc).
    lines: an iterable of G-code lines."""
    pts, x, y, z, feat, started = [], None, None, 0.0, "", False
    num = re.compile(r"([XYZEFIJR])(-?[\d.]+)")
    for ln, line in enumerate(lines, 1):
        if line.startswith("; Z_HEIGHT:"):
            z = _num(line.split(":", 1)[1].strip(), "G-code line %d" % ln); started = True
            if not 0 <= z <= MAX_COORD_MM:
                raise InputError("G-code line %d: Z_HEIGHT %g out of range" % (ln, z))
            continue
        if line.startswith("; FEATURE:"):
            feat = line[10:].strip(); continue
        g = line[:3]
        if g not in ("G1 ", "G0 ", "G2 ", "G3 "):
            continue
        where = "G-code line %d" % ln
        v = dict((k, _num(n, where)) for k, n in num.findall(line.split(";")[0]))
        if any(abs(v.get(k, 0)) > MAX_COORD_MM for k in "XYIJR"):
            raise InputError("%s: a coordinate past %g mm" % (where, MAX_COORD_MM))
        nx, ny = v.get("X", x), v.get("Y", y)
        # a move is sampled only once both X and Y are known (before that there is no start point to draw from)
        if (started and feat not in ("Custom", "") and v.get("E", 0) > 0 and None not in (x, y, nx, ny)):
            arc = _arc(x, y, nx, ny, v, g == "G2 ", where) if g in ("G2 ", "G3 ") else None
            L = max(arc[2], arc[3]) * abs(arc[5]) if arc else math.hypot(nx - x, ny - y)
            if L > MAX_SEGMENT_MM:
                raise InputError("%s: one extruding move of %.0f mm (no bed is that big)" % (where, L))
            n = max(1, int(L / 0.4))
            if len(pts) + n + 1 > MAX_POINTS:
                raise InputError("G-code: over %d sample points; a sliced tile has about half a million" % MAX_POINTS)
            for i in range(n + 1):
                t = i / n
                if arc:
                    cx, cy, r0, r1, a0, sw = arc
                    r, a_ = r0 + (r1 - r0) * t, a0 + sw * t
                    pts.append((cx + r * math.cos(a_), cy + r * math.sin(a_), z, feat))
                else:
                    pts.append((x + (nx - x) * t, y + (ny - y) * t, z, feat))
        x, y = nx, ny
    return pts


def read_features(path):
    """(W, D, features) from OpenSCAD's echo output."""
    if os.path.getsize(path) > MAX_FEATURES_BYTES:
        raise InputError("%s: over %d KB; the tile's echo output is a few KB" % (path, MAX_FEATURES_BYTES // 1024))
    w, d, feats = W, D, []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        text = fh.read(MAX_FEATURES_BYTES + 1)
    for ln, line in enumerate(text.splitlines(), 1):
        where = "%s line %d" % (path, ln)
        t = re.search(r'TILE;([^;"]+);([^;"]+)', line)
        if t:
            w, d = _num(t.group(1), where), _num(t.group(2), where)
            if not (0 < w <= MAX_COORD_MM and 0 < d <= MAX_COORD_MM):
                raise InputError("%s: tile size %g x %g out of range" % (where, w, d))
        m = re.search(r'FEAT;([^"]*)', line)
        if m:
            parts = m.group(1).split(";")
            if len(parts) != 6:
                raise InputError("%s: expected FEAT;name;x;y;z;r;kind, got %d fields" % (where, len(parts) + 1))
            n, x, y, z, r, k = parts
            f = dict(name=n, x=_num(x, where), y=_num(y, where), z=_num(z, where), r=_num(r, where), kind=k.strip())
            if not 0 < f["r"] <= MAX_RADIUS_MM:
                raise InputError("%s: search radius must be > 0 and <= %g mm" % (where, MAX_RADIUS_MM))
            if f["kind"] not in KINDS:
                raise InputError("%s: kind %r is not one of %s" % (where, safe_text(f["kind"], 20), ", ".join(KINDS)))
            if max(abs(f["x"]), abs(f["y"]), abs(f["z"])) > MAX_COORD_MM:
                raise InputError("%s: a coordinate past %g mm" % (where, MAX_COORD_MM))
            feats.append(f)
            if len(feats) > MAX_FEATURES:
                raise InputError("%s: over %d features (the tile has a few dozen)" % (path, MAX_FEATURES))
    if not feats:
        raise InputError("%s: no FEAT lines (use the ECHO output of tile.scad)" % path)
    return w, d, feats


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("gcode"); ap.add_argument("features"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        sys.exit(run(a))
    except InputError as e:
        print("check_gcode: refused: %s" % e, file=sys.stderr)
        sys.exit(2)
    except OSError as e:
        print("check_gcode: %s" % e, file=sys.stderr)
        sys.exit(2)


def run(a):
    W, D, feats = read_features(a.features)
    pts = samples(read_gcode(a.gcode))
    if not pts:
        raise InputError("%s: no extrusions after the first '; Z_HEIGHT:' line (is it a sliced Bambu Studio file?)" % a.gcode)
    z0 = min(p[2] for p in pts)
    first = [p for p in pts if abs(p[2] - z0) < 1e-6]
    cx = (min(p[0] for p in first) + max(p[0] for p in first)) / 2
    cy = (min(p[1] for p in first) + max(p[1] for p in first)) / 2
    dx, dy = cx - W / 2, cy - D / 2
    span = (max(p[0] for p in first) - min(p[0] for p in first), max(p[1] for p in first) - min(p[1] for p in first))
    grid = {}
    for p in pts:
        grid.setdefault((int((p[0] - dx) // 2), int((p[1] - dy) // 2)), []).append(p)
    checks = [0]
    def near(f):
        out = []
        for gx in range(int((f["x"] - f["r"]) // 2), int((f["x"] + f["r"]) // 2) + 1):
            for gy in range(int((f["y"] - f["r"]) // 2), int((f["y"] + f["r"]) // 2) + 1):
                cell = grid.get((gx, gy), [])
                checks[0] += len(cell)
                if checks[0] > MAX_NEAR_CHECKS:
                    raise InputError("features: over %d point checks; search radii too large for this G-code" % MAX_NEAR_CHECKS)
                for p in cell:
                    if math.hypot(p[0] - dx - f["x"], p[1] - dy - f["y"]) <= f["r"]:
                        out.append(p)
        return out
    res, bad = [], 0
    for f in feats:
        ps = near(f)
        if f["kind"] == "bridge":
            hits = [p for p in ps if p[3] == "Bridge" and abs(p[2] - f["z"]) <= 0.25]
            ok = bool(hits); got = "Bridge at z %.2f" % hits[0][2] if hits else "no Bridge extrusion at z %.2f" % f["z"]
        else:
            top = max((p[2] for p in ps), default=None)
            tol_lo = 0.8 if f["kind"] == "tip" else 0.25
            ok = top is not None and -tol_lo - 1e-6 <= top - f["z"] <= 0.25 + 1e-6
            got = "top z %.2f" % top if top is not None else "nothing printed here"
            if f["kind"] == "info":
                got += " (info only)" if ok else " (info only: missing or short in this G-code)"
                res.append(dict(feature=f["name"], expected_z=f["z"], kind=f["kind"], result=got, ok=True, printed=ok)); continue
        bad += not ok
        res.append(dict(feature=f["name"], expected_z=f["z"], kind=f["kind"], result=got, ok=ok))
    summary = dict(features=len(res), failed=bad, first_layer_span_mm=[round(s, 2) for s in span],
                   max_z=round(max(p[2] for p in pts), 2),
                   overhang_wall_z=sorted({round(p[2], 2) for p in pts if p[3] == "Overhang wall"})[:3] + ["..."],
                   bridge_layers_z=sorted({round(p[2], 2) for p in pts if p[3] == "Bridge"}))
    if a.json:
        print(json.dumps(dict(summary=summary, features=res), indent=1))
    else:
        for r in res:
            print("  %-4s %-30s expect %5.2f  %s" % ("ok" if r["ok"] else "FAIL", safe_text(r["feature"]), r["expected_z"], r["result"]))
        print("summary:", json.dumps(summary))
    return 1 if bad else 0


if __name__ == "__main__":
    main()
