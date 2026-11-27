"""Check an STL: bounding box, shells (connected pieces), closed/manifold edges, volume per shell.
Usage: python3 check_stl.py file.stl [--expect-shells N] [--json]   (prints at most 50 shells; --json adds shells_total)
Exit 1 if a shell is open/non-manifold, the mesh is empty or the shell count differs from --expect-shells; exit 2 if
the file is not a well-formed STL or is past the size cap (refused, never read slowly)."""
import sys, struct, json, math, os, argparse
from collections import Counter

MAX_STL_BYTES = 64 * 1024 * 1024       # the tile is about 11 MB (binary, ~230 K triangles)
MAX_TRIANGLES = 500000                 # ~2x the tile (230 720); 1 M random triangles took 41 s and 2.4 GB
MAX_VERTICES = 300000                  # distinct vertices; a closed mesh has about half as many as triangles
                                       # (the tile: 115 340), while loose triangles have three each
MAX_SHELLS = 10000                     # separate pieces; the tile has 7. Past this the mesh is refused before the
                                       # per-shell work (random triangles make one shell each)
MAX_LINE_BYTES = 1024 * 1024           # an ASCII STL line is under 100 bytes; a longer one is refused
MAX_LINES = 4000000                    # ASCII STL lines: 7 per triangle, so 500 K triangles is 3.5 M
MAX_SHELLS_SHOWN = 50                  # shells printed (and kept in --json); the rest are counted
CHUNK = 1024 * 1024
RECORD = struct.Struct("<12fH")         # binary STL: normal, 3 vertices, attribute word = 50 bytes
BATCH = 20000                           # records read at a time


class StlError(Exception):
    """Not a well-formed STL, or past a cap; the CLI prints it and exits 2."""


def _finite(vals, where):
    if not all(math.isfinite(v) for v in vals):
        raise StlError("%s: a vertex coordinate is not a finite number" % where)


def _binary(fh, n):
    left, i = n, 0
    while left:
        k = min(left, BATCH)
        data = fh.read(RECORD.size * k)
        if len(data) != RECORD.size * k:
            raise StlError("binary STL ends early")
        recs = list(RECORD.iter_unpack(data))
        # one check per batch: float32 values never overflow a float64 sum, so the sum is finite exactly when
        # every vertex coordinate is (any NaN or infinity makes it NaN or infinite)
        if not math.isfinite(math.fsum(c for f in recs for c in f[3:12])):
            for j, f in enumerate(recs):
                _finite(f[3:12], "triangle %d" % (i + j + 1))
        for f in recs:
            yield (f[3:6], f[6:9], f[9:12])
        i += k
        left -= k


def _lines(fh):
    """(line number, bytes) of a binary stream, read in chunks; a line over MAX_LINE_BYTES is refused before it is
    kept whole. Splits on \n, \r\n and \r, like text mode did."""
    buf, ln, cr = b"", 0, False
    while True:
        chunk = fh.read(CHUNK)
        if not chunk:
            break
        if cr and chunk.startswith(b"\n"):
            chunk = chunk[1:]                              # a \r\n split across two chunks is one line end
        cr = chunk.endswith(b"\r")
        parts = (buf + chunk).replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
        buf = parts.pop()
        if len(buf) > MAX_LINE_BYTES:
            raise StlError("line %d: over %d KB; an ASCII STL line is short" % (ln + len(parts) + 1, MAX_LINE_BYTES // 1024))
        if ln + len(parts) > MAX_LINES:                   # counted before any per-line work (a flood of blank lines)
            raise StlError("over %d lines; an ASCII STL has 7 per triangle" % MAX_LINES)
        for line in parts:
            ln += 1
            yield ln, line
    if buf:
        yield ln + 1, buf


def _ascii(path):
    v, count = [], 0
    with open(path, "rb") as fh:
        for ln, line in _lines(fh):
            s = line.lstrip()
            if not s.startswith(b"vertex"):                # cheap prefix test before any split
                continue
            parts = s.split()
            if parts[0] != b"vertex":
                continue
            if len(parts) != 4:
                raise StlError("line %d: a vertex needs exactly 3 numbers" % ln)
            try:
                p = tuple(float(t) for t in parts[1:])
            except ValueError:
                raise StlError("line %d: a vertex coordinate is not a number" % ln) from None
            _finite(p, "line %d" % ln)
            v.append(p)
            if len(v) == 3:
                count += 1
                if count > MAX_TRIANGLES:
                    raise StlError("over %d triangles" % MAX_TRIANGLES)
                yield tuple(v); v = []
    if v:
        raise StlError("the file ends in the middle of a triangle")


def read_stl(path):
    """Triangles of a binary or ASCII STL, one at a time. Binary is recognised by its exact size
    (84 + 50 x the triangle count in the header), so a binary file whose header starts with "solid" still reads."""
    size = os.path.getsize(path)
    if size > MAX_STL_BYTES:
        raise StlError("%s is over %d MB (the tile is about 11 MB)" % (path, MAX_STL_BYTES // 2 ** 20))
    with open(path, "rb") as fh:
        head = fh.read(84)
        if len(head) == 84:
            n = struct.unpack("<I", head[80:84])[0]
            if 84 + RECORD.size * n == size:
                if n > MAX_TRIANGLES:
                    raise StlError("over %d triangles" % MAX_TRIANGLES)
                for t in _binary(fh, n):
                    yield t
                return
        if not head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"solid"):
            if len(head) < 84:
                raise StlError("%s: %d bytes, too short for an STL" % (path, size))
            raise StlError("%s: binary header says %d triangles (%d bytes) but the file is %d bytes, and it is not ASCII STL"
                           % (path, n, 84 + RECORD.size * n, size))
    for t in _ascii(path):
        yield t


def check(path):
    """Shells, edges, volume and bounding box of the mesh. Lean on purpose: faces are one flat list of vertex ids
    and edges are int keys, so memory stays near 1 KB per triangle; past MAX_SHELLS pieces the mesh is refused
    before any per-shell work."""
    ids, verts, faces = {}, [], []                      # faces: a0, b0, c0, a1, b1, c1, ... (vertex ids)
    get = ids.get
    for t in read_stl(path):
        f = []
        for p in t:
            k = (round(p[0], 4), round(p[1], 4), round(p[2], 4))
            i = get(k)
            if i is None:
                i = ids[k] = len(verts); verts.append(k)
                if i >= MAX_VERTICES:
                    raise StlError("over %d distinct vertices (the tile has about 115 000); a mesh of loose "
                                   "triangles, not a printable part" % MAX_VERTICES)
            f.append(i)
        a, b, c = f
        if a != b and b != c and a != c:
            faces += f
    del ids, get
    if not faces:
        return {"file": path, "triangles": 0, "bbox_min": None, "bbox_max": None, "size": None, "bad_edges": 0,
                "shells": [], "volume_cm3": 0.0}
    nv = len(verts)
    parent = list(range(nv))
    def find(a):
        while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for j in range(0, len(faces), 3):
        a, b, c = faces[j], faces[j + 1], faces[j + 2]
        for x, y in ((a, b), (b, c)):
            rx, ry = find(x), find(y)
            if rx != ry: parent[rx] = ry
    shell_of = {}                                       # root vertex -> shell index
    for j in range(0, len(faces), 3):
        r = find(faces[j])
        if r not in shell_of:
            shell_of[r] = len(shell_of)
            if len(shell_of) > MAX_SHELLS:
                raise StlError("over %d separate shells (the tile has 7); not a printable part" % MAX_SHELLS)
    # each directed edge x->y once as the int x * nv + y; closed and manifold means each undirected edge is used
    # exactly once in each direction
    edges = Counter(e for j in range(0, len(faces), 3)
                    for e in (faces[j] * nv + faces[j + 1], faces[j + 1] * nv + faces[j + 2],
                              faces[j + 2] * nv + faces[j]))
    bad = 0
    for e, n in edges.items():
        x, y = divmod(e, nv)
        rev = edges.get(y * nv + x, 0)
        if (x < y or not rev) and (n != 1 or rev != 1):
            bad += 1
    del edges
    ns = len(shell_of)
    count, vol = [0] * ns, [0.0] * ns
    lo_s, hi_s = [[1e9] * 3 for _ in range(ns)], [[-1e9] * 3 for _ in range(ns)]
    for j in range(0, len(faces), 3):
        a = faces[j]
        si = shell_of[find(a)]
        count[si] += 1
        p, q, r = verts[a], verts[faces[j + 1]], verts[faces[j + 2]]
        vol[si] += (p[0] * (q[1] * r[2] - q[2] * r[1]) - p[1] * (q[0] * r[2] - q[2] * r[0])
                    + p[2] * (q[0] * r[1] - q[1] * r[0])) / 6
        mn, mx = lo_s[si], hi_s[si]
        for v in (p, q, r):
            for i in range(3):
                if v[i] < mn[i]: mn[i] = v[i]
                if v[i] > mx[i]: mx[i] = v[i]
    lo = [min(m[i] for m in lo_s) for i in range(3)]
    hi = [max(m[i] for m in hi_s) for i in range(3)]
    out = {"file": path, "triangles": len(faces) // 3, "bbox_min": [round(x, 3) for x in lo],
           "bbox_max": [round(x, 3) for x in hi], "size": [round(hi[i] - lo[i], 3) for i in range(3)], "bad_edges": bad,
           "shells": sorted(({"volume_cm3": round(vol[i] / 1000, 3), "faces": count[i],
                              "min": [round(x, 2) for x in lo_s[i]], "max": [round(x, 2) for x in hi_s[i]]}
                             for i in range(ns)), key=lambda s: -s["volume_cm3"])}
    out["volume_cm3"] = round(sum(s["volume_cm3"] for s in out["shells"]), 3)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("stl"); ap.add_argument("--expect-shells", type=int); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        r = check(a.stl)
    except (StlError, OSError) as e:
        print("check_stl: refused: %s" % e, file=sys.stderr)
        sys.exit(2)
    ok = r["triangles"] > 0 and r["bad_edges"] == 0 and (a.expect_shells is None or len(r["shells"]) == a.expect_shells)
    r["ok"] = ok
    shown = r["shells"][:MAX_SHELLS_SHOWN]
    more = len(r["shells"]) - len(shown)
    if a.json:
        print(json.dumps(dict(r, shells=shown, shells_total=len(r["shells"])), indent=1))
    elif not r["triangles"]:
        print("%s: no triangles -> PROBLEM" % a.stl)
    else:
        print("%s: size %s mm, bbox %s..%s, %d shells, %.2f cm3, bad edges %d -> %s" % (
            a.stl, r["size"], r["bbox_min"], r["bbox_max"], len(r["shells"]), r["volume_cm3"], r["bad_edges"], "OK" if ok else "PROBLEM"))
        for s in shown: print("   shell %.3f cm3  %s..%s" % (s["volume_cm3"], s["min"], s["max"]))
        if more: print("   ... and %d more shells" % more)
    sys.exit(0 if ok else 1)
