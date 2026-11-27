"""Shared helpers for the telltile tests. Every fixture is built here in a temp folder; none is committed."""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

MB = 2 ** 20


def clean_env():
    env = dict(os.environ)
    for k in ("FILAMENT_REGISTRY", "BAMBU_KNOWN_KEYS", "BAMBU_STUDIO_PROFILES"):
        env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(script, *args, cwd=None, timeout=60):
    """Run a skill script; returns (returncode, stdout, stderr)."""
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args), cwd=cwd, env=clean_env(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def setting(value, tid="T-20261127-P1S-01"):
    return {"value": value, "tests": [tid], "why": "test"}


def registry(filament=None, process=None, hexv="#1B2A4A", changed=None, **extra):
    """A one-filament, one-printer registry dict with the given tuned blocks."""
    f = {"code": "PLA-NAVY-01", "brand": "unknown", "product_line": "unknown", "material": "PLA",
         "color": {"name": "Navy", "hex": hexv, "hex_is_estimate": True}, "diameter_mm": 1.75, "inventory": "",
         "drying": "", "presets": {},
         "tuned": {"P1S": {"status": "tuned", "filament": filament or {}, "process": process or {}}},
         "tests": [{"id": "T-20261127-P1S-01", "date": "2026-11-27", "printer": "P1S", "tile": "tuning tile v1",
                    "settings": "x", "changed": changed or {"filament": {}, "process": {}}, "photos": "",
                    "features": {}, "verdict": "ok", "note": ""}]}
    f.update(extra)
    return {"schema": "gnoshcodes filament registry v1", "updated": "", "notes": [],
            "printers": {"P1S": {"machine_preset": "Bambu Lab P1S 0.4 nozzle", "process_preset": "0.20mm Standard @BBL X1C",
                                 "plate": "Textured PEI Plate", "nozzle": "0.4"}},
            "filaments": [f]}


def binary_stl(path, tris, header=b"binary stl", count=None):
    with open(path, "wb") as fh:
        fh.write(header.ljust(80, b" ")[:80])
        fh.write(struct.pack("<I", len(tris) if count is None else count))
        for t in tris:
            fh.write(struct.pack("<12fH", 0, 0, 0, *[c for p in t for c in p], 0))


def cube_tris(s=10.0):
    """A closed, outward-facing cube of side s at the origin (12 triangles)."""
    v = [(x, y, z) for x in (0, s) for y in (0, s) for z in (0, s)]
    q = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    out = []
    for a, b, c, d in q:
        out += [(v[a], v[b], v[c]), (v[a], v[c], v[d])]
    return out


def tile_gcode(ox=50.0, oy=50.0, with_top=True, with_bridge=True):
    """A tiny 'sliced tile': a 112 x 84 first-layer outline at (ox, oy), a top at model (16, 50) z 6 and a Bridge
    at model (30, 20) z 2."""
    L = ["; HEADER", "M83", "; Z_HEIGHT: 0.2", "; FEATURE: Outer wall", "G1 X%.3f Y%.3f" % (ox, oy)]
    for x, y in ((112, 0), (112, 84), (0, 84), (0, 0)):
        L.append("G1 X%.3f Y%.3f E0.5" % (ox + x, oy + y))
    if with_bridge:
        L += ["; Z_HEIGHT: 2.0", "; FEATURE: Bridge", "G0 X%.3f Y%.3f" % (ox + 29, oy + 20),
              "G1 X%.3f Y%.3f E0.1" % (ox + 31, oy + 20)]
    if with_top:
        L += ["; Z_HEIGHT: 6.0", "; FEATURE: Top surface", "G0 X%.3f Y%.3f" % (ox + 15, oy + 50),
              "G1 X%.3f Y%.3f E0.1" % (ox + 17, oy + 50)]
    return "\n".join(L) + "\n"


FEATURES = 'ECHO: "TILE;112;84"\nECHO: "FEAT;boss;16;50;6;1.5;top"\nECHO: "FEAT;bridge 2mm;30;20;2;1.5;bridge"\n'


def text(*parts):
    """Contents of a file under the skill folder (or an absolute path)."""
    with open(os.path.join(SKILL, *parts), encoding="utf-8") as fh:
        return fh.read()


class Patch(object):
    """Set module attributes for one test (the scripts read their caps at call time)."""
    def __init__(self, test, mod, **kw):
        for k, v in kw.items():
            old = getattr(mod, k)
            test.addCleanup(setattr, mod, k, old)
            setattr(mod, k, v)


class TempDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ptt-test-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, data):
        p = self.path(name)
        with open(p, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as fh:
            fh.write(data)
        return p

    def write_registry(self, reg, name="filaments.json"):
        return self.write(name, json.dumps(reg, indent=1))

    def read_bytes(self, p):
        with open(p, "rb") as fh:
            return fh.read()

    def gcode_3mf(self, name, entries):
        """A zip with {entry name: bytes or iterable of byte chunks}, streamed so large entries stay cheap."""
        p = self.path(name)
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for n, data in entries.items():
                if isinstance(data, bytes):
                    z.writestr(n, data)
                else:
                    with z.open(n, "w", force_zip64=True) as fh:
                        for chunk in data:
                            fh.write(chunk)
        return p
