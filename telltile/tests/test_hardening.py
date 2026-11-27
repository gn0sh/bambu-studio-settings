"""Hardening tests: hostile or malformed registries, G-code, 3mf and STL files are refused cleanly, and the docs
carry the safety rules. Every hostile input is built here; none is committed. Memory tests never allocate gigabytes:
they show the input is refused (by size, ratio or a cap lowered for the test) before the costly work starts."""
import argparse
import io
import json
import os
import struct
import unittest
import unittest.mock

import zipfile

import ptt_fixtures as fx
import check_gcode
import check_stl
import registry

S = fx.setting
zipfile_real = zipfile.ZipFile
EVIL_GCODE = "M104 S300"


Patch = fx.Patch


class RegistryCase(fx.TempDir):
    def reg(self, *args):
        return fx.run("registry.py", "--registry", self.path("filaments.json"), *args)


# ---------------------------------------------------------------- registry: only allowlisted tuning keys and plain values
class SettingAllowlist(RegistryCase):
    HOSTILE = [
        ("process post_process", dict(process={"post_process": S("C:\\evil.exe")}), "post_process"),
        ("process Post_Process", dict(process={"Post_Process": S("x")}), "post_process"),
        ("filament start gcode", dict(filament={"filament_start_gcode": S(EVIL_GCODE)}), "G-code"),
        ("process FILAMENT_END_GCODE", dict(process={"FILAMENT_END_GCODE": S("x")}), "G-code"),
        ("machine start gcode", dict(filament={"machine_start_gcode": S("x")}), "G-code"),
        ("unknown key", dict(filament={"wall_loops": S("3")}), "not a tuning setting"),
        ("wrong block", dict(process={"nozzle_temperature": S("215")}), "is a filament setting"),
        ("nested list value", dict(filament={"nozzle_temperature": S(["215", "M104"])}), "not a list"),
        ("nested dict value", dict(filament={"nozzle_temperature": S({"a": 1})}), "not an object"),
        ("newline in value", dict(process={"seam_position": S("aligned\nM104 S300")}), "is not one of nearest"),
        ("long value", dict(process={"seam_position": S("a" * 65)}), "65 characters (max 32)"),
        ("bool value", dict(process={"thick_bridges": S(True)}), "true/false is not accepted"),
        ("bare value", dict(filament={"nozzle_temperature": "215"}), "must be an object, not a string"),
        ("tampered hex", dict(hexv='#000000"; post_process=x'), "color.hex: a string of 24 characters (max 7)"),
    ]

    def test_spec_refuses(self):
        for name, kw, why in self.HOSTILE:
            with self.subTest(name):
                self.write_registry(fx.registry(**kw))
                for mode in ("tune", "preset", "process-preset"):
                    rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S", "--for", mode)
                    self.assertNotEqual(rc, 0, out)
                    self.assertEqual(out, "", "spec printed a spec for %s" % name)
                    self.assertIn(why, err)

    def test_validate_refuses(self):
        for name, kw, why in self.HOSTILE:
            with self.subTest(name):
                self.write_registry(fx.registry(**kw))
                rc, out, err = self.reg("validate")
                self.assertEqual(rc, 1, out)
                self.assertNotIn("registry OK", out)
                self.assertIn(why, err)
                self.assertIn("filaments[0]", err)
                self.assertNotIn("Traceback", err)

    def test_validate_flags_history(self):
        self.write_registry(fx.registry(changed={"filament": {"filament_start_gcode": EVIL_GCODE},
                                                 "process": {"post_process": "x"}}))
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1)
        self.assertIn("filaments[0].tests[0].changed.filament: filament_start_gcode: G-code settings", err)
        self.assertIn("filaments[0].tests[0].changed.process: post_process: post_process is never", err)

    def test_record_refuses_cli(self):
        p = self.write_registry(fx.registry())
        before = self.read_bytes(p)
        for flag, kv, why in (("--set-process", "post_process=C:\\evil.exe", "post_process"),
                              ("--set-filament", "filament_start_gcode=" + EVIL_GCODE, "G-code"),
                              ("--set-filament", "change_filament_gcode=x", "G-code"),
                              ("--set-filament", "xy_hole_compensation=0.05", "is a process setting"),
                              ("--set-process", "nozzle_temperature=215", "is a filament setting"),
                              ("--set-filament", "wall_loops=3", "not a tuning setting"),
                              ("--set-filament", "nozzle_temperature=215\nM104 S300", "is not int 165..300"),
                              ("--set-filament", "nozzle_temperature=" + "9" * 65, "(max 32)"),
                              ("--set-filament", "nozzle_temperature=400", "400' is not int 165..300"),
                              ("--set-process", "seam_position=$(touch x)", "is not one of nearest")):
            with self.subTest(kv):
                rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x", flag, kv)
                self.assertNotEqual(rc, 0)
                self.assertIn(why, err)
                self.assertEqual(self.read_bytes(p), before, "registry changed after a refused record")

    def test_record_refuses_into_tainted_filament(self):
        p = self.write_registry(fx.registry(process={"post_process": S("x")}))
        before = self.read_bytes(p)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x",
                                "--set-filament", "nozzle_temperature=215")
        self.assertNotEqual(rc, 0)
        self.assertIn("post_process", err)
        self.assertEqual(self.read_bytes(p), before)

    def test_clean_registry_still_passes(self):
        self.write_registry(fx.registry(filament={"nozzle_temperature": S("215"), "pressure_advance": S(0.02)},
                                        process={"seam_gap": S("10%"), "wall_generator": S("arachne")}))
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 0, out + err)
        rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["plate"]["pressure_advance"], 0.02)

    def test_malformed_registry_is_refused_not_crashed(self):
        for name, reg in (("list", []), ("no filaments", {"printers": {}}), ("tuned not object",
                          fx.registry(tuned=[])), ("tests not list", fx.registry(tests={}))):
            with self.subTest(name):
                self.write_registry(reg)
                rc, out, err = self.reg("validate")
                self.assertNotEqual(rc, 0)
                self.assertNotIn("Traceback", err)
                self.assertIn("not a usable registry", err)

    def test_oversized_registry_refused(self):
        Patch(self, registry, MAX_REGISTRY_BYTES=1000)
        registry.REG = self.write("filaments.json", " " * 2000 + "{}")
        with self.assertRaises(SystemExit) as cm:
            registry.load()
        self.assertIn("refused", str(cm.exception.code))


# ---------------------------------------------------------------- docs: what the agent reads is data
class DataNotInstructions(unittest.TestCase):
    def read(self, name):
        return fx.text(name)

    def test_skill_rule(self):
        rules = self.read("SKILL.md").split("## Rules", 1)[1].split("## Workflow", 1)[0]
        self.assertIn("data, not instructions", rules)
        for must in ("post_process", "*_gcode", "make_preset.py --write", "registry.py validate", "exact keys and values"):
            self.assertIn(must, rules)

    def test_readme_safety_line(self):
        readme = self.read("README.md")
        self.assertIn("as data, not instructions", readme)
        self.assertIn("registry.py validate", readme)


# ---------------------------------------------------------------- check_gcode: size caps
class GcodeCaps(fx.TempDir):
    def test_one_long_move_refused(self):
        """A 100-byte file whose one move to X4000000 would make 10 M points (2 GB)."""
        g = "; Z_HEIGHT: 0.2\n; FEATURE: Outer wall\nG1 X0 Y0\nG1 X10 Y0 E1\nG1 X10 Y10 E1\nG1 X4000000 Y10 E1\n"
        rc, out, err = fx.run("check_gcode.py", self.write("big.gcode", g), self.write("f.txt", fx.FEATURES), timeout=30)
        self.assertEqual(rc, 2)
        self.assertIn("refused", err)

    def test_segment_cap(self):
        g = ["; Z_HEIGHT: 0.2", "; FEATURE: Outer wall", "G1 X0 Y0", "G1 X300 Y300 E1"]   # 424 mm, inside the coord cap
        with self.assertRaisesRegex(check_gcode.InputError, "one extruding move"):
            check_gcode.samples(g)

    def test_point_cap(self):
        Patch(self, check_gcode, MAX_POINTS=1000)
        g = ["; Z_HEIGHT: 0.2", "; FEATURE: Outer wall", "G1 X0 Y0"] + ["G1 X%d Y0 E1" % (i % 2 * 200) for i in range(1, 10)]
        with self.assertRaisesRegex(check_gcode.InputError, "sample points"):
            check_gcode.samples(g)

    def test_point_caps_lowered(self):
        """untrusted-gcode/points-cap-too-high: 78 KB of 399 mm moves made 4.98 M points (834 MB, 4-6 s). The caps
        are ~2x the real P1S slice (507 K points, < 0.2 M point checks); the same file is refused quickly now."""
        import time
        self.assertEqual((check_gcode.MAX_POINTS, check_gcode.MAX_NEAR_CHECKS), (1000000, 5000000))
        g = ["; Z_HEIGHT: 0.2", "; FEATURE: Outer wall", "G1 X0 Y0"] + ["G1 X%d Y0 E1" % (i % 2 * 399)
                                                                        for i in range(1, 6000)]
        t = time.perf_counter()
        with self.assertRaisesRegex(check_gcode.InputError, "over 1000000 sample points"):
            check_gcode.samples(g)
        self.assertLess(time.perf_counter() - t, 1.0)

    def test_moves_before_x_and_y_are_known(self):
        """untrusted-gcode/unset-axis-traceback: `G1 X5` then `G1 X6 E1` (Y never set) was a TypeError traceback,
        for G1 and G2/G3 alike. A move is sampled only once X and Y are both known."""
        head = ["; Z_HEIGHT: 0.2", "; FEATURE: Outer wall"]
        for moves in (["G1 X5", "G1 X6 E1"], ["G1 Y5", "G1 Y6 E1"], ["G1 X5", "G2 X6 I1 J0 E1"],
                      ["G1 Y5", "G3 Y6 R2 E1"], ["G2 X6 Y1 I1 J0 E1"], ["G1 E1"], ["G0 X1", "G1 Y2 E1", "G1 X3 E1"]):
            with self.subTest(moves):
                pts = check_gcode.samples(head + moves)
                if moves[-1] == "G1 X3 E1":                     # X and Y both known by then: 2 mm sampled
                    self.assertEqual([(p[0], p[1]) for p in pts[::5]], [(1.0, 2.0), (3.0, 2.0)])
                else:
                    self.assertEqual(pts, [])
        rc, out, err = fx.run("check_gcode.py", self.write("a.gcode", "\n".join(head + ["G1 X5", "G1 X6 E1",
                                                                                         "G2 X7 I1 J0 E1"]) + "\n"),
                              self.write("f.txt", fx.FEATURES))
        self.assertEqual(rc, 2, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("no extrusions", err)

    def test_non_finite_refused(self):
        for line in ("G1 X%s Y0 E1" % ("9" * 400), "; Z_HEIGHT: nan", "; Z_HEIGHT: inf"):
            with self.subTest(line[:20]):
                with self.assertRaises(check_gcode.InputError):
                    check_gcode.samples(["; Z_HEIGHT: 0.2", "; FEATURE: Outer wall", "G1 X0 Y0", line])

    def test_3mf_entry_over_cap_refused_before_reading(self):
        """17 MB of G-code inside a small 3mf."""
        chunk = b"G1 X1 Y1 E0.1\n" * (fx.MB // 14)
        p = self.gcode_3mf("bomb.3mf", {"Metadata/plate_1.gcode": (chunk for _ in range(17))})
        self.assertLess(os.path.getsize(p), 2 * fx.MB)
        rc, out, err = fx.run("check_gcode.py", p, self.write("f.txt", fx.FEATURES), timeout=30)
        self.assertEqual(rc, 2)
        self.assertIn("cap 16 MB", err)

    def test_caps_lowered(self):
        """Fresh review 51e0562 items 7-8: 63 MB of G-code took 14.5 s, so the cap is 16 MB; the directory 1 MB."""
        self.assertEqual(check_gcode.MAX_GCODE_BYTES, 16 * fx.MB)
        self.assertEqual(check_gcode.MAX_CD_BYTES, fx.MB)

    def test_3mf_ratio_refused(self):
        p = self.gcode_3mf("ratio.3mf", {"Metadata/plate_1.gcode": b"G1 X1 Y1 E0.1\n" * 200000})
        rc, out, err = fx.run("check_gcode.py", p, self.write("f.txt", fx.FEATURES))
        self.assertEqual(rc, 2)
        self.assertIn("compresses more than 200:1", err)

    def test_plain_file_over_cap_refused(self):
        Patch(self, check_gcode, MAX_GCODE_BYTES=1000)
        p = self.write("big.gcode", "; x\n" * 1000)
        with self.assertRaisesRegex(check_gcode.InputError, "over"):
            list(check_gcode.read_gcode(p))

    def test_chunked_reader_counts_real_bytes(self):
        """A stream longer than its declared size is still cut off at the cap."""
        Patch(self, check_gcode, MAX_GCODE_BYTES=4096, CHUNK=1024)
        with self.assertRaises(check_gcode.InputError):
            list(check_gcode._lines(io.BytesIO(b"; x\n" * 5000), "stream"))

    def test_blank_line_flood_refused(self):
        """untrusted-gcode/line-flood-slow: 16 MB of blank lines took 4.2 s (3.8) to say "no extrusions". The line
        count is capped (2 M; the real P1S slice has 129 804) before any line is handed on."""
        import time
        self.assertEqual(check_gcode.MAX_GCODE_LINES, 2000000)
        p = self.write("blank.gcode", b"\n" * (4 * fx.MB))
        t = time.perf_counter()
        with self.assertRaisesRegex(check_gcode.InputError, "over 2000000 lines"):
            list(check_gcode.read_gcode(p))
        self.assertLess(time.perf_counter() - t, 1.0)
        Patch(self, check_gcode, MAX_GCODE_LINES=5)
        self.assertEqual(len(list(check_gcode._lines(io.BytesIO(b"a\nb\n\nc\nd\n"), "s"))), 5)
        with self.assertRaisesRegex(check_gcode.InputError, "over 5 lines"):
            list(check_gcode._lines(io.BytesIO(b"\n" * 6 + b"x"), "s"))

    def test_long_line_refused(self):
        Patch(self, check_gcode, MAX_LINE_BYTES=100, CHUNK=64)
        with self.assertRaisesRegex(check_gcode.InputError, "a line over"):
            list(check_gcode._lines(io.BytesIO(b"x" * 1000), "stream"))


# ---------------------------------------------------------------- check_gcode: clear errors on malformed input
class GcodeErrors(fx.TempDir):
    def check(self, gcode_path, feats=fx.FEATURES):
        return fx.run("check_gcode.py", gcode_path, self.write("f.txt", feats), timeout=30)

    def assertRefused(self, res, text):
        rc, out, err = res
        self.assertEqual(rc, 2, out + err)
        self.assertNotIn("Traceback", err)
        self.assertIn(text, err)

    def test_3mf_without_gcode(self):
        self.assertRefused(self.check(self.gcode_3mf("a.3mf", {"3D/3dmodel.model": b"<model/>"})), "no .gcode inside")

    def test_not_a_zip(self):
        self.assertRefused(self.check(self.write("a.3mf", b"not a zip")), "not a readable 3mf")

    def test_no_extrusions(self):
        self.assertRefused(self.check(self.write("a.gcode", "; nothing\nG28\n")), "no extrusions")

    def test_bad_number(self):
        g = "; Z_HEIGHT: 0.2\n; FEATURE: Outer wall\nG1 X1.2.3 Y0 E1\n"
        self.assertRefused(self.check(self.write("a.gcode", g)), "line 3: '1.2.3' is not a number")

    def test_bad_z_height(self):
        self.assertRefused(self.check(self.write("a.gcode", "; Z_HEIGHT: abc\n")), "not a number")

    def test_short_feat_line(self):
        self.assertRefused(self.check(self.write("a.gcode", fx.tile_gcode()), 'ECHO: "FEAT;a;1;2"\n'), "expected FEAT")

    def test_bad_radius(self):
        for r in ("1e9", "0", "-1", "51"):
            with self.subTest(r):
                self.assertRefused(self.check(self.write("a.gcode", fx.tile_gcode()), 'ECHO: "FEAT;a;1;2;3;%s;top"\n' % r),
                                   "search radius")

    def test_bad_kind_and_no_features(self):
        g = self.write("a.gcode", fx.tile_gcode())
        self.assertRefused(self.check(g, 'ECHO: "FEAT;a;1;2;3;1;sideways"\n'), "is not one of")
        self.assertRefused(self.check(g, "nothing here\n"), "no FEAT lines")

    def test_bad_tile_size(self):
        self.assertRefused(self.check(self.write("a.gcode", fx.tile_gcode()), 'ECHO: "TILE;1.2.3;84"\n' + fx.FEATURES),
                           "not a number")

    def test_too_many_features(self):
        Patch(self, check_gcode, MAX_FEATURES=3)
        p = self.write("f.txt", "".join('ECHO: "FEAT;a%d;1;2;3;1;top"\n' % i for i in range(5)))
        with self.assertRaisesRegex(check_gcode.InputError, "over 3 features"):
            check_gcode.read_features(p)

    def test_point_check_budget(self):
        Patch(self, check_gcode, MAX_NEAR_CHECKS=10)
        a = argparse.Namespace(gcode=self.write("a.gcode", fx.tile_gcode()),
                               features=self.write("f.txt", 'ECHO: "FEAT;a;56;42;0.2;50;top"\n'), json=True)
        with self.assertRaisesRegex(check_gcode.InputError, "point checks"):
            check_gcode.run(a)


# ---------------------------------------------------------------- check_stl: caps and malformed input
class StlChecks(fx.TempDir):
    def tris(self, p):
        return list(check_stl.read_stl(p))

    def test_size_cap(self):
        Patch(self, check_stl, MAX_STL_BYTES=500)
        p = self.path("c.stl"); fx.binary_stl(p, fx.cube_tris())     # 684 bytes
        with self.assertRaisesRegex(check_stl.StlError, "over"):
            self.tris(p)

    def test_triangle_cap(self):
        Patch(self, check_stl, MAX_TRIANGLES=5)
        p = self.path("c.stl"); fx.binary_stl(p, fx.cube_tris())
        with self.assertRaisesRegex(check_stl.StlError, "over 5 triangles"):
            self.tris(p)

    def test_count_must_match_length(self):
        for count in (0xFFFFFFFF, 13, 11):
            with self.subTest(count):
                p = self.path("c.stl"); fx.binary_stl(p, fx.cube_tris(), count=count)
                with self.assertRaisesRegex(check_stl.StlError, "binary header says"):
                    self.tris(p)

    def test_too_short(self):
        with self.assertRaisesRegex(check_stl.StlError, "too short"):
            self.tris(self.write("s.stl", b"abc"))

    def test_ascii_arity(self):
        p = self.write("a.stl", "solid x\nfacet normal 0 0 1\nouter loop\nvertex 1 2\nvertex 1 2 3\nvertex 1 2 3\n")
        with self.assertRaisesRegex(check_stl.StlError, "line 4: a vertex needs exactly 3 numbers"):
            self.tris(p)
        p = self.write("b.stl", "solid x\nvertex 1 2 3 4\n")
        with self.assertRaisesRegex(check_stl.StlError, "exactly 3"):
            self.tris(p)

    def test_ascii_mid_triangle_and_bad_number(self):
        with self.assertRaisesRegex(check_stl.StlError, "middle of a triangle"):
            self.tris(self.write("a.stl", "solid x\nvertex 1 2 3\n"))
        with self.assertRaisesRegex(check_stl.StlError, "not a number"):
            self.tris(self.write("b.stl", "solid x\nvertex 1 2 q\n"))

    def test_non_finite(self):
        for v in ("nan", "inf", "-inf", "1e999"):
            with self.subTest("ascii " + v):
                with self.assertRaisesRegex(check_stl.StlError, "finite"):
                    self.tris(self.write("a.stl", "solid x\nvertex %s 0 0\nvertex 0 1 0\nvertex 0 0 1\n" % v))
        t = [((float("nan"), 0, 0), (0, 1, 0), (0, 0, 1))]
        p = self.path("n.stl"); fx.binary_stl(p, t)
        with self.assertRaisesRegex(check_stl.StlError, "finite"):
            self.tris(p)

    def test_empty_mesh(self):
        p = self.path("e.stl"); fx.binary_stl(p, [])
        r = check_stl.check(p)
        self.assertEqual((r["triangles"], r["shells"], r["size"]), (0, [], None))
        rc, out, err = fx.run("check_stl.py", p)
        self.assertEqual(rc, 1, err)
        self.assertIn("no triangles", out)
        rc, out, err = fx.run("check_stl.py", p, "--json")
        self.assertEqual((rc, json.loads(out)["ok"]), (1, False))

    def test_caps(self):
        self.assertEqual((check_stl.MAX_TRIANGLES, check_stl.MAX_STL_BYTES), (500000, 64 * fx.MB))
        self.assertEqual((check_stl.MAX_VERTICES, check_stl.MAX_SHELLS), (300000, 10000))

    def test_loose_triangles_refused_quickly(self):
        """untrusted-stl/cap-still-slow: 1 M random triangles took 41 s and 2.45 GB. Loose triangles have three
        vertices each, so they pass MAX_VERTICES long before the triangle cap and are refused in well under a
        second (measured: 500 K random in 0.5 s, 108 MB; a closed 500 K-triangle torus, the worst valid mesh
        under the cap, 3.8 s and 288 MB; the tile 1.5-1.9 s)."""
        import random, time
        rnd = random.Random(5)
        n = 120000
        p = self.path("loose.stl")
        with open(p, "wb") as fh:
            fh.write(b"loose".ljust(80) + struct.pack("<I", n))
            rec = struct.Struct("<12fH")
            fh.write(b"".join(rec.pack(0, 0, 0, *[rnd.uniform(0, 100) for _ in range(9)], 0) for _ in range(n)))
        t = time.perf_counter()
        with self.assertRaisesRegex(check_stl.StlError, "over 300000 distinct vertices"):
            check_stl.check(p)
        self.assertLess(time.perf_counter() - t, 1.5)

    def test_shell_cap(self):
        Patch(self, check_stl, MAX_SHELLS=2)
        p = self.path("cubes.stl")
        fx.binary_stl(p, [tuple(tuple(c + 20 * k for c in v) for v in t) for k in range(3) for t in fx.cube_tris()])
        with self.assertRaisesRegex(check_stl.StlError, "over 2 separate shells"):
            check_stl.check(p)
        Patch(self, check_stl, MAX_SHELLS=3)
        r = check_stl.check(p)
        self.assertEqual((len(r["shells"]), r["bad_edges"], r["triangles"]), (3, 0, 36))
        self.assertEqual(r["volume_cm3"], 3.0)

    def test_vertex_cap(self):
        Patch(self, check_stl, MAX_VERTICES=7)
        p = self.path("c.stl"); fx.binary_stl(p, fx.cube_tris())          # 8 corners
        with self.assertRaisesRegex(check_stl.StlError, "over 7 distinct vertices"):
            check_stl.check(p)

    def test_ascii_blank_line_flood_refused(self):
        """untrusted-stl/line-flood-slow: `solid x` + 64 MB of blank lines took 9.4 s (3.8). Lines are capped at
        4 M (7 per triangle: 500 K triangles is 3.5 M), counted before any per-line work."""
        import time
        self.assertEqual(check_stl.MAX_LINES, 4000000)
        p = self.write("blank.stl", b"solid x\n" + b"\n" * (5 * fx.MB))
        t = time.perf_counter()
        with self.assertRaisesRegex(check_stl.StlError, "over 4000000 lines"):
            self.tris(p)
        self.assertLess(time.perf_counter() - t, 1.0)
        Patch(self, check_stl, MAX_LINES=8)
        tri = b"facet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\n"
        self.assertEqual(len(self.tris(self.write("one.stl", b"solid x\n" + tri))), 1)       # 8 lines: at the cap
        with self.assertRaisesRegex(check_stl.StlError, "over 8 lines"):
            self.tris(self.write("two.stl", b"solid x\n" + tri + b"endsolid x\n"))

    def test_ascii_long_line_refused_before_split(self):
        """A one-line ASCII STL is refused at the line cap, never held whole or split (review 51e0562 item 5)."""
        Patch(self, check_stl, MAX_LINE_BYTES=1000, CHUNK=256)
        for body in (b"vertex " + b"1 " * 5000, b"x" * 5000, b"facet " * 2000):
            with self.subTest(body[:10]):
                with self.assertRaisesRegex(check_stl.StlError, "line 2: over"):
                    self.tris(self.write("l.stl", b"solid x\n" + body + b"\nendsolid\n"))

    def test_ascii_line_ends(self):
        """\n, \r\n (also split across read chunks) and \r alone all end a line, as text mode did."""
        Patch(self, check_stl, CHUNK=7)
        tri = ["vertex 0 0 0", "vertex 1 0 0", "vertex 0 1 0"]
        for nl in ("\n", "\r\n", "\r"):
            with self.subTest(repr(nl)):
                p = self.write("a.stl", nl.join(["solid x", "facet normal 0 0 1", "outer loop"] + tri + ["endsolid"]))
                self.assertEqual(self.tris(p), [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))])
        p = self.write("b.stl", "solid x\r\n\r\nvertex 1 2\r\n")
        with self.assertRaisesRegex(check_stl.StlError, "line 3: a vertex needs"):
            self.tris(p)

    def test_vertex_prefix(self):
        p = self.write("a.stl", "solid x\nvertexes 1 2 3 4 5\n  vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n")
        self.assertEqual(len(self.tris(p)), 1)

    def test_shell_output_capped(self):
        """200 K shells printed 12.5 MB; now 50 lines and a count, and --json keeps 50 plus shells_total."""
        tris = []
        for i in range(60):
            tris += [tuple((x + 20 * i, y, z) for x, y, z in t) for t in fx.cube_tris(5.0)]
        p = self.path("many.stl"); fx.binary_stl(p, tris)
        rc, out, err = fx.run("check_stl.py", p)
        self.assertEqual(rc, 0, err)
        self.assertIn("60 shells", out)
        self.assertEqual(out.count("   shell "), 50)
        self.assertIn("... and 10 more shells", out)
        rc, out, err = fx.run("check_stl.py", p, "--json")
        r = json.loads(out)
        self.assertEqual((len(r["shells"]), r["shells_total"], r["ok"]), (50, 60, True))
        rc, out, err = fx.run("check_stl.py", p, "--expect-shells", "60")
        self.assertEqual(rc, 0, out)

    def test_cli_refuses_cleanly(self):
        p = self.path("c.stl"); fx.binary_stl(p, fx.cube_tris(), count=0xFFFFFFFF)
        rc, out, err = fx.run("check_stl.py", p)
        self.assertEqual(rc, 2)
        self.assertNotIn("Traceback", err)


# ---------------------------------------------------------------- registry: planted temp files are not followed
class NoSymlinkFollow(RegistryCase):
    def test_planted_tmp_and_bak_symlinks_not_followed(self):
        p = self.write_registry(fx.registry())
        target = self.write("target.txt", "keep me\n")
        try:
            os.symlink(target, p + ".tmp")
            os.symlink(target, p + ".bak.tmp")
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("cannot make symlinks here")
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x",
                                "--set-filament", "nozzle_temperature=215")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_bytes(target), b"keep me\n")
        self.assertIn("nozzle_temperature", fx.text(p))
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".tmp") and not os.path.islink(self.path(n))]
        self.assertEqual(leftovers, [])

    def test_bak_symlink_replaced_not_written_through(self):
        p = self.write_registry(fx.registry())
        target = self.write("target.txt", "keep me\n")
        try:
            os.symlink(target, p + ".bak")
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("cannot make symlinks here")
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_bytes(target), b"keep me\n")
        self.assertFalse(os.path.islink(p + ".bak"))


HOSTILE = {"brand": "Acme $(id -un)@$(hostname)", "line": "`whoami` %USERNAME% !!", "color": "Navy $HOME ;|&",
           "inventory": "'single' \"double\" \\back", "drying": "%PATH% & del *"}


class FromFile(RegistryCase):
    """skill-guidance/free-text-args-in-shell: free text read off photos and labels went on the command line in
    double quotes, where bash ran $(id -un) before registry.py saw it. add and record take every field from a
    --from JSON file (written with the agent's file tool); shell syntax in it is stored literally."""
    def setUp(self):
        super().setUp()
        reg = fx.registry()
        reg["filaments"][0]["presets"] = {"P1S": "Bambu PLA Basic @BBL P1S 0.4 nozzle"}
        self.p = self.write_registry(reg)

    def fields(self, name, **fields):
        return self.write(name, json.dumps(fields))

    def show(self, code):
        rc, out, err = self.reg("show", code)
        self.assertEqual(rc, 0, err)
        return json.loads(out)

    def test_add_stores_shell_syntax_literally(self):
        f = self.fields("new.json", code="PLA-RED-01", material="PLA", hex="#FF0000", hex_exact=True,
                        preset={"P1S": "Bambu PLA Basic @BBL P1S 0.4 nozzle"}, **HOSTILE)
        rc, out, err = self.reg("add", "--from", f)
        self.assertEqual(rc, 0, err)
        got = self.show("PLA-RED-01")
        self.assertEqual((got["brand"], got["product_line"], got["color"]["name"], got["inventory"], got["drying"]),
                         (HOSTILE["brand"], HOSTILE["line"], HOSTILE["color"], HOSTILE["inventory"], HOSTILE["drying"]))
        self.assertEqual((got["color"]["hex_is_estimate"], got["presets"]), (False, {"P1S": "Bambu PLA Basic @BBL P1S 0.4 nozzle"}))

    def test_record_stores_shell_syntax_literally(self):
        f = self.fields("rec.json", code="PLA-NAVY-01", printer="P1S", verdict="strings `id`; $(uname -a)",
                        note="%COMPUTERNAME% $USER", why="it's \"better\"", feature={"stringing": "bad", "fit": "0.20"},
                        set_filament={"filament_retraction_length": "1.0", "nozzle_temperature": 215},
                        set_process={"xy_hole_compensation": 0.05}, status="tuned")
        before = self.read_bytes(self.p)
        rc, out, err = self.reg("record", "--from", f, "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_bytes(self.p), before)
        self.assertIn("strings `id`; $(uname -a)", json.loads(out.split("\n(dry run")[0])["verdict"])
        rc, out, err = self.reg("record", "--from", f)
        self.assertEqual(rc, 0, err)
        got = self.show("PLA-NAVY-01")
        test = got["tests"][-1]
        self.assertEqual((test["verdict"], test["note"], test["features"]),
                         ("strings `id`; $(uname -a)", "%COMPUTERNAME% $USER", {"stringing": "bad", "fit": "0.20"}))
        t = got["tuned"]["P1S"]
        self.assertEqual((t["status"], t["filament"]["nozzle_temperature"]["value"],
                          t["filament"]["filament_retraction_length"]["why"], t["process"]["xy_hole_compensation"]["value"]),
                         ("tuned", 215, 'it\'s "better"', 0.05))

    @unittest.skipIf(os.name == "nt" or not os.path.exists("/bin/bash"), "needs bash")
    def test_documented_command_under_bash(self):
        """The documented flow run by a real shell: the command line holds only paths, so nothing expands."""
        import shlex, subprocess, sys
        f = self.fields("new.json", code="PLA-RED-01", material="PLA", hex="#FF0000", **HOSTILE)
        cmd = "%s %s --registry %s add --from %s" % tuple(shlex.quote(x) for x in (
            sys.executable, os.path.join(fx.SCRIPTS, "registry.py"), self.p, f))
        p = subprocess.run(["/bin/bash", "-c", cmd], env=fx.clean_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.show("PLA-RED-01")["brand"], "Acme $(id -un)@$(hostname)")

    def test_bad_files_refused_nothing_written(self):
        before = self.read_bytes(self.p)
        cases = [("record", '{"code": "PLA-NAVY-01", "printer": "P1S", "verdict": "x", "post_process": "a"}', "not a field"),
                 ("record", '{"code": "PLA-NAVY-01", "printer": "P1S", "verdict": ["x"]}', "must be a string"),
                 ("record", '{"code": "PLA-NAVY-01", "printer": "P1S", "verdict": "x", "feature": {"a": 1}}', "object of strings"),
                 ("record", '{"code": "PLA-NAVY-01", "printer": "P1S", "verdict": "x", "set_filament": {"nozzle_temperature": true}}', "strings or numbers"),
                 ("record", '{"code": "PLA-NAVY-01", "printer": "P1S", "verdict": "x", "status": "great"}', "status must be"),
                 ("record", '{"code": "PLA-NAVY-01", "printer": "P1S"}', "needs verdict"),
                 ("record", '{"code": "PLA-NAVY-01", "printer": "P1S", "verdict": "x", "set_filament": {"start_gcode": "M104"}}', "G-code"),
                 ("record", '{"code": "PLA-NAVY-01", "printer": "P1S", "verdict": "' + "A" * 501 + '"}', "at most 500"),
                 ("record", '{"code": "PLA-NAVY-01", "verdict": "x", "verdict": "y"}', "duplicate key"),
                 ("record", '["code"]', "one JSON object"),
                 ("record", "{" * 100, "not JSON"),
                 ("record", '{"code": NaN}', "not JSON"),
                 ("record", b"\xff\xfe", "not JSON"),
                 ("record", '{"x": "' + "A" * (70 * 1024) + '"}', "over 64 KB"),
                 ("add", '{"code": "PLA-RED-01", "material": "PLA", "color": "Red", "hex": "#FF0000", "brand": "a\u001b[2J"}', "control"),
                 ("add", '{"code": "PLA RED 01", "material": "PLA", "color": "Red", "hex": "#FF0000"}', "MATERIAL-COLOR-NN"),
                 ("add", '{"material": "PLA", "color": "Red", "hex": "#FF0000"}', "needs code")]
        for cmd, text, why in cases:
            with self.subTest(text[:50]):
                rc, out, err = self.reg(cmd, "--from", self.write("f.json", text))
                self.assertNotEqual(rc, 0, out)
                self.assertNotIn("Traceback", err)
                self.assertIn(why, err)
                self.assertEqual(self.read_bytes(self.p), before)

    def test_from_file_grown_after_size_check_refused(self):
        """merge_from re-checks the length after reading (a file that grows between getsize and read)."""
        f = self.write("big.json", '{"x": "' + "A" * (70 * 1024) + '"}')
        a = argparse.Namespace(cmd="record", from_=f)
        with unittest.mock.patch("os.path.getsize", return_value=10):
            with self.assertRaises(SystemExit) as cm:
                registry.merge_from(a)
        self.assertIn("over 64 KB", str(cm.exception))

    def test_field_given_twice_refused(self):
        f = self.fields("rec.json", code="PLA-NAVY-01", printer="P1S", verdict="x")
        rc, out, err = self.reg("record", "--from", f, "--verdict", "y")
        self.assertNotEqual(rc, 0)
        self.assertIn("verdict is given both", err)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--from", f)
        self.assertIn("code is given both", err)
        rc, out, err = self.reg("record", "--from", self.path("missing.json"))
        self.assertEqual(rc, 1); self.assertNotIn("Traceback", err)

    def test_docs_never_put_free_text_on_a_command_line(self):
        """Every documented add/record command takes its fields from --from: no flag but --registry, --from and
        --dry-run, and no quoted text."""
        import glob, re
        docs = [fx.text("SKILL.md"), fx.text("README.md")] + [fx.text(p) for p in glob.glob(os.path.join(fx.SKILL, "references", "*.md"))]
        found = 0
        for doc in docs:
            for line in doc.splitlines():
                for m in re.finditer(r"registry\.py\b[^`]*?\b(add|record)\b([^`]*)", line):
                    found += 1
                    flags = set(re.findall(r"--[a-z-]+", m.group(2)))
                    self.assertLessEqual(flags, {"--from", "--dry-run"}, line)
                    self.assertIn("--from", flags, line)
                    self.assertNotRegex(m.group(2), "[\"']", line)
        self.assertGreaterEqual(found, 3)
        self.assertIn("never goes on a command line", fx.text("SKILL.md"))
        self.assertIn("--from", fx.text("references", "photo-instructions.md"))


class FilesystemErrors(RegistryCase):
    """registry-write/non-regular-traceback: a directory at <registry>.bak or at the registry path gave an
    IsADirectoryError traceback. Any OSError is one refusal line; nothing is written."""
    def test_bak_is_a_directory(self):
        p = self.write_registry(fx.registry())
        before = self.read_bytes(p)
        os.mkdir(p + ".bak")
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x")
        self.assertEqual(rc, 1, err)
        self.assertNotIn("Traceback", err)
        self.assertTrue(err.startswith("refused: ") and err.count("\n") == 1, err)
        self.assertEqual(self.read_bytes(p), before)
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])

    def test_registry_is_a_directory(self):
        os.mkdir(self.path("filaments.json"))
        for args in (("spec", "PLA-NAVY-01", "--printer", "P1S"), ("list",), ("validate",)):
            with self.subTest(args[0]):
                rc, out, err = self.reg(*args)
                self.assertEqual(rc, 1, err)
                self.assertNotIn("Traceback", err)
                self.assertIn("refused: ", err)


class SymlinkedRegistry(RegistryCase):
    """registry-write/backup-follows-symlink: filaments.json linked to a 0600 file plus `init --force` copied that
    file into .bak files as 0644. A registry path that is a symlink (or not a regular file) is never backed up or
    replaced; a normal registry's .bak files keep its mode."""
    def link(self, target):
        try:
            os.symlink(target, self.path("filaments.json"))
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("cannot make symlinks here")
        return self.path("filaments.json")

    def assertNoBak(self):
        self.assertEqual([n for n in os.listdir(self.dir) if ".bak" in n], [])

    def test_init_force_through_symlink_refused(self):
        secret = self.write("secret.txt", "token=do-not-copy\n"); os.chmod(secret, 0o600)
        p = self.link(secret)
        rc, out, err = self.reg("init", "--force", "--printer", "P1S=a|b|c")
        self.assertNotEqual(rc, 0)
        self.assertIn("symlink or not a regular file", err)
        self.assertNotIn("Traceback", err)
        self.assertNoBak()
        self.assertTrue(os.path.islink(p))
        self.assertEqual(self.read_bytes(secret), b"token=do-not-copy\n")

    def test_write_through_symlink_refused(self):
        real = self.write_registry(fx.registry(), name="real.json")
        before = self.read_bytes(real)
        self.link(real)
        for args in (("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x"),
                     ("add", "PLA-RED-01", "--material", "PLA", "--color", "Red", "--hex", "#FF0000")):
            with self.subTest(args[0]):
                rc, out, err = self.reg(*args)
                self.assertNotEqual(rc, 0)
                self.assertIn("symlink or not a regular file", err)
                self.assertEqual(self.read_bytes(real), before)
                self.assertNoBak()
        rc, out, err = self.reg("validate")                 # reading through the link still works
        self.assertEqual(rc, 0, err)

    def test_dangling_symlink_not_replaced(self):
        p = self.link(self.path("missing.json"))
        rc, out, err = self.reg("init", "--printer", "P1S=a|b|c")
        self.assertNotEqual(rc, 0)
        self.assertIn("exists", err)
        rc, out, err = self.reg("init", "--force", "--printer", "P1S=a|b|c")
        self.assertNotEqual(rc, 0)
        self.assertIn("symlink", err)
        self.assertTrue(os.path.islink(p))
        self.assertFalse(os.path.exists(self.path("missing.json")))

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_bak_keeps_source_mode(self):
        p = self.write_registry(fx.registry()); os.chmod(p, 0o600)
        mode = lambda q: oct(os.stat(q).st_mode & 0o777)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x")
        self.assertEqual(rc, 0, err)
        self.assertEqual((mode(p), mode(p + ".bak")), ("0o600", "0o600"))
        os.chmod(p + ".bak", 0o644)                          # an older, looser .bak takes the registry's mode
        rc, out, err = self.reg("init", "--force", "--printer", "P1S=a|b|c")
        self.assertEqual(rc, 0, err)
        kept = [n for n in os.listdir(self.dir) if ".before-init-" in n]
        self.assertEqual([mode(self.path(n)) for n in kept], ["0o600"])
        self.assertEqual((mode(p), mode(p + ".bak")), ("0o600", "0o600"))


# ---------------------------------------------------------------- registry: never in the skill folder
class NoSkillFolderRegistry(RegistryCase):
    def test_default_is_current_folder(self):
        """A filaments.json next to the script is never picked up (the old fallback)."""
        old = os.environ.pop("FILAMENT_REGISTRY", None)
        self.addCleanup(lambda: old is not None and os.environ.__setitem__("FILAMENT_REGISTRY", old))
        self.write_registry(fx.registry())
        Patch(self, registry, HERE=self.dir)
        self.assertEqual(registry.registry_path(None), os.path.join(os.getcwd(), "filaments.json"))
        self.assertNotEqual(os.path.dirname(registry.registry_path(None)), self.dir)

    def test_no_fallback_next_to_script(self):
        rc, out, err = fx.run("registry.py", "list", cwd=self.dir)
        self.assertNotEqual(rc, 0)
        self.assertIn(os.path.join(self.dir, "filaments.json"), err)

    def test_write_inside_skill_folder_refused(self):
        for folder in (fx.SKILL, fx.SCRIPTS):
            p = os.path.join(folder, "filaments.json")
            self.assertFalse(os.path.exists(p))
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))
            rc, out, err = fx.run("registry.py", "--registry", p, "init", "--printer", "P1S=a|b|c")
            self.assertNotEqual(rc, 0)
            self.assertIn("inside the skill folder", err)
            self.assertFalse(os.path.exists(p))


# ---------------------------------------------------------------- registry: backups
class Backups(RegistryCase):
    def test_save_keeps_bak(self):
        p = self.write_registry(fx.registry())
        before = self.read_bytes(p)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.read_bytes(p + ".bak"), before)
        self.assertNotEqual(self.read_bytes(p), before)

    def test_init_force_keeps_old_records(self):
        p = self.write_registry(fx.registry())
        before = self.read_bytes(p)
        rc, out, err = self.reg("init", "--force", "--printer", "P1S=a|b|c")
        self.assertEqual(rc, 0, err)
        kept = [n for n in os.listdir(self.dir) if ".before-init-" in n]
        self.assertEqual(len(kept), 1)
        self.assertEqual(self.read_bytes(self.path(kept[0])), before)
        self.reg("add", "PLA-RED-01", "--material", "PLA", "--color", "Red", "--hex", "#FF0000")
        self.assertEqual(self.read_bytes(self.path(kept[0])), before, "a later save overwrote the init backup")

    def test_init_without_force_refuses(self):
        p = self.write_registry(fx.registry())
        before = self.read_bytes(p)
        rc, out, err = self.reg("init", "--printer", "P1S=a|b|c")
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.read_bytes(p), before)


# ---------------------------------------------------------------- registry: terminal-safe output
class TerminalSafe(RegistryCase):
    BAD = ("\x1b", "\x07", "\x9b", "\u202e", "\x00")

    def test_controls_refused_never_echoed(self):
        """Control and format characters in any registry text refuse the whole file (TEXT and SAFE fields), and the
        refusal itself prints none of them."""
        reg = fx.registry(brand="x\x1b[31mRED\x9b2J", material="PLA\u202e", product_line="a\x07b")
        reg["filaments"][0]["color"]["name"] = "Navy\x1b[2J\x1b]0;pwned\x07"
        reg["printers"]["P1S"]["process_preset"] = "evil\x1b]0;t\x07"
        self.write_registry(reg)
        for args in (("list",), ("show", "PLA-NAVY-01"), ("spec", "PLA-NAVY-01", "--printer", "P1S"),
                     ("spec", "PLA-NAVY-01", "--printer", "P1S", "--for", "process-preset"),
                     ("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x", "--dry-run")):
            with self.subTest(args[0]):
                rc, out, err = self.reg(*args)
                self.assertNotEqual(rc, 0, out)
                self.assertEqual(out, "")
                self.assertIn("control or format character", err)
                for ch in self.BAD:
                    self.assertNotIn(ch, out + err, "%r reached the terminal from %s" % (ch, args[0]))

    def test_error_messages_strip_controls(self):
        reg = fx.registry()
        reg["printers"]["P\x1b[2J"] = {}
        self.write_registry(reg)
        rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "X1C")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("\x1b", err)
        rc, out, err = self.reg("validate")
        self.assertNotIn("\x1b", out + err)

    def test_safe_text(self):
        self.assertEqual(registry.safe_text("a\x1b[2Jb\u202ec"), "a?[2Jb?c")
        self.assertEqual(registry.safe_text("Grün"), "Grün")


# ---------------------------------------------------------------- docs: licence scope and footprint
class Wording(unittest.TestCase):
    def test_readme_marks_line(self):
        text = fx.text("README.md")
        self.assertIn("grants no rights to the gnoshcodes name or\nmarks", text)

    def test_footprint_matches_tile(self):
        scad = fx.text("assets", "tile.scad")
        self.assertIn("112 x 84 mm, fits any bed of 120 x 90 mm or more", scad)
        self.assertIn("Fits any bed of 120 x 90 mm or more", fx.text("references", "tile-spec.md"))
        self.assertIn("W = 112;   D = 84;", scad)
        self.assertEqual((check_gcode.W, check_gcode.D), (112.0, 84.0))


# ---------------------------------------------------------------- registry: full-string patterns
class TrailingNewline(RegistryCase):
    GOOD = {"NUM_RE": "0.98", "INT_RE": "215", "PCT_RE": "10%", "HEX_RE": "#1B2A4A", "CODE_RE": "PLA-NAVY-01",
            "PRINTER_RE": "P1S", "DATE_RE": "2026-11-27", "DATE_OR_EMPTY_RE": "2026-11-27", "SAFE_RE": "Textured PEI",
            "TEST_ID_RE": "T-20261127-P1S-01", "FEATURE_KEY_RE": "fit", "FEATURE_VALUE_RE": "0.20"}

    def test_every_regex_rejects_trailing_newline(self):
        for name, good in self.GOOD.items():
            with self.subTest(name):
                rx = getattr(registry, name)
                self.assertTrue(rx.fullmatch(good))
                self.assertIsNone(rx.fullmatch(good + "\n"))
                self.assertIsNone(rx.match(good + "\n"), "%s still ends in $" % name)

    def test_value(self):
        self.write_registry(fx.registry(process={"bridge_flow": S("0.98\n")}))
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1); self.assertIn("process.bridge_flow.value", err)
        rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S")
        self.assertNotEqual(rc, 0); self.assertEqual(out, "")
        self.write_registry(fx.registry())
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x",
                                "--set-process", "bridge_flow=0.98\n\n", "--dry-run")
        # parse_kv strips the CLI value, so the newline never reaches the regex; a stored one is the case above
        self.assertEqual(rc, 0, err)

    def test_hex(self):
        self.write_registry(fx.registry(hexv="#1B2A4A\n"))
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1); self.assertIn("color.hex: a string of 8 characters (max 7)", err)
        rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S")
        self.assertNotEqual(rc, 0); self.assertEqual(out, "")
        self.reg("init", "--force", "--printer", "P1S=a|b|c")
        rc, out, err = self.reg("add", "PLA-RED-01", "--material", "PLA", "--color", "Red", "--hex", "#FF0000\n")
        self.assertNotEqual(rc, 0); self.assertIn("--hex", err)

    def test_code(self):
        reg = fx.registry(); reg["filaments"][0]["code"] = "PLA-NAVY-01\n"
        self.write_registry(reg)
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1); self.assertIn("filaments[0].code: 'PLA-NAVY-01?' is not a code", err)
        self.reg("init", "--force", "--printer", "P1S=a|b|c")
        rc, out, err = self.reg("add", "PLA-RED-01\n", "--material", "PLA", "--color", "Red", "--hex", "#FF0000")
        self.assertNotEqual(rc, 0); self.assertIn("MATERIAL-COLOR-NN", err)

    def test_printer(self):
        reg = fx.registry()
        reg["printers"]["P1S\n"] = reg["printers"].pop("P1S")
        reg["filaments"][0]["tuned"]["P1S\n"] = reg["filaments"][0]["tuned"].pop("P1S")
        reg["filaments"][0]["tests"][0]["printer"] = "P1S\n"
        self.write_registry(reg)
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1)
        self.assertIn("printers: key 'P1S?' is not a printer name", err)
        rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S\n")
        self.assertNotEqual(rc, 0); self.assertEqual(out, ""); self.assertIn("printer name", err)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S\n", "--verdict", "x")
        self.assertNotEqual(rc, 0)

    def test_tuned_printer_key_checked_even_if_listed(self):
        """A tuned key that is not a valid printer name is reported even when printers lists it too."""
        reg = fx.registry()
        reg["filaments"][0]["tuned"]["X1C\n"] = {"status": "untested", "filament": {}, "process": {}}
        self.write_registry(reg)
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1); self.assertIn("filaments[0].tuned: key 'X1C?' is not a printer name", err)

    def test_date(self):
        self.write_registry(fx.registry())
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x", "--date", "2026-11-27\n")
        self.assertNotEqual(rc, 0); self.assertIn("--date", err)
        reg = fx.registry(); reg["filaments"][0]["tests"][0]["date"] = "2026-11-27\n"
        self.write_registry(reg)
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1); self.assertIn("tests[0].date: a string of 11 characters (max 10)", err)


# ---------------------------------------------------------------- check_gcode: zip directory caps before zipfile
def eocd(entries, cd_size, comment=b""):
    return struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, entries, entries, cd_size, 0, len(comment)) + comment


def zip64_tail(entries, cd_size):
    rec = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, entries, entries, cd_size, 0)
    loc = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)
    return rec + loc + eocd(0xFFFF, 0xFFFFFFFF)


class DirectoryBeforeZipfile(fx.TempDir):
    def setUp(self):
        super().setUp()
        def no_zipfile(*a, **k):
            raise AssertionError("zipfile.ZipFile opened before the directory caps")
        Patch(self, check_gcode.zipfile, ZipFile=no_zipfile)

    def refused(self, data, text):
        p = self.write("crafted.3mf", data)
        self.assertLess(len(data), 70000)
        with self.assertRaisesRegex(check_gcode.InputError, text):
            list(check_gcode.read_gcode(p))

    def test_huge_directory(self):
        self.refused(b"\0" * 100 + eocd(5, 140 * fx.MB), "zip directory of 140 MB")

    def test_too_many_entries(self):
        self.refused(b"\0" * 100 + eocd(60000, 1000), "60000 entries")

    def test_with_comment(self):
        self.refused(b"\0" * 100 + eocd(5, 140 * fx.MB, comment=b"c" * 1000), "zip directory")

    def test_declared_count_must_fit_directory(self):
        """Thousands of entries declared as 1: the directory size gives it away (review 51e0562 item 7)."""
        self.refused(b"\0" * 100 + eocd(1, 900000), "for 1 declared entries")
        self.refused(b"\0" * 100 + eocd(0, 2000), "for 0 declared entries")

    def test_zip64_record(self):
        """untrusted-gcode/zip64-locator-bypass: any zip64 locator is refused, whatever its record says, whether
        the record sits right before it or the locator points elsewhere (Python 3.13 follows reloff)."""
        self.refused(b"\0" * 100 + zip64_tail(2500000, 140 * fx.MB), "zip64")
        self.refused(b"\0" * 100 + zip64_tail(5, 1000), "zip64")
        far = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, 5400000, 5400000, 200 * fx.MB, 0)
        loc = struct.pack("<4sLQL", b"PK\x06\x07", 0, 0, 1)                    # reloff 0: the record at the start
        self.refused(far + b"\0" * 1000 + loc + eocd(1, 46), "zip64")
        self.refused(loc + eocd(1, 46), "zip64")                                # locator at offset 0

    def test_no_end_record(self):
        self.refused(b"\0" * 100, "no zip end record")
        self.refused(b"\0" * 100 + b"PK\x05\x06\0\0", "no zip end record")

    def test_cli(self):
        Patch(self, check_gcode.zipfile, ZipFile=zipfile_real)
        p = self.write("crafted.3mf", b"\0" * 100 + eocd(5, 140 * fx.MB))
        rc, out, err = fx.run("check_gcode.py", p, self.write("f.txt", fx.FEATURES))
        self.assertEqual(rc, 2); self.assertIn("zip directory", err)


class ZipMethods(fx.TempDir):
    """untrusted-gcode/bzip2-lzma-unbounded-decompress: only stored and deflated entries are read; bzip2 and LZMA
    readers inflate without an output bound on Python 3.8 (927 bytes became 1 GB), so they are refused before
    z.open."""
    def method_3mf(self, method):
        p = self.path("m%d.3mf" % method)
        with zipfile.ZipFile(p, "w", method) as z:
            z.writestr("Metadata/plate_1.gcode", fx.tile_gcode())
        return p

    def test_bzip2_and_lzma_refused_before_open(self):
        for name, method, module in (("bzip2", zipfile.ZIP_BZIP2, "bz2"), ("lzma", zipfile.ZIP_LZMA, "lzma")):
            with self.subTest(name):
                try:
                    __import__(module)
                except ImportError:
                    self.skipTest("no %s module" % module)
                p = self.method_3mf(method)
                def no_open(*a, **k):
                    raise AssertionError("z.open called for a %s entry" % name)
                with unittest.mock.patch.object(zipfile.ZipFile, "open", no_open):
                    with self.assertRaisesRegex(check_gcode.InputError, "compression method %d" % method):
                        list(check_gcode.read_gcode(p))
                rc, out, err = fx.run("check_gcode.py", p, self.write("f.txt", fx.FEATURES))
                self.assertEqual(rc, 2, err)
                self.assertIn("stored or deflated", err)
                self.assertNotIn("Traceback", err)

    def test_stored_and_deflated_read(self):
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(method):
                lines = list(check_gcode.read_gcode(self.method_3mf(method)))
                self.assertTrue(any(l.startswith("; Z_HEIGHT") for l in lines))

    def test_real_zip64_archive_refused(self):
        """A zip that zipfile itself wrote with zip64 end records (forced by a zero file-count limit)."""
        p = self.path("z64.3mf")
        with unittest.mock.patch.object(zipfile, "ZIP_FILECOUNT_LIMIT", 0):
            with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("Metadata/plate_1.gcode", fx.tile_gcode())
        with open(p, "rb") as fh:
            self.assertIn(b"PK\x06\x07", fh.read())
        rc, out, err = fx.run("check_gcode.py", p, self.write("f.txt", fx.FEATURES))
        self.assertEqual(rc, 2, err)
        self.assertIn("zip64", err)

    def test_corrupt_deflate_stream(self):
        """untrusted-gcode/uncaught-zip-errors: a damaged deflate stream raised zlib.error (a traceback, exit 1)."""
        p = self.gcode_3mf("bad.3mf", {"Metadata/plate_1.gcode": fx.tile_gcode().encode() * 20})
        with open(p, "rb") as fh:
            data = bytearray(fh.read())
        start = data.index(b"Metadata/plate_1.gcode") + len("Metadata/plate_1.gcode")   # local header name end
        for k in range(start, start + 40):
            data[k] = 0xFF                                     # the first deflate bytes: an invalid block type
        p = self.write("bad.3mf", bytes(data))
        with self.assertRaisesRegex(check_gcode.InputError, "cannot read"):
            list(check_gcode.read_gcode(p))
        rc, out, err = fx.run("check_gcode.py", p, self.write("f.txt", fx.FEATURES))
        self.assertEqual(rc, 2, err)
        self.assertNotIn("Traceback", err)

    def test_utf8_flagged_name_not_utf8(self):
        """A central-directory name flagged UTF-8 but holding 0xFF raised UnicodeDecodeError from ZipFile()."""
        p = self.gcode_3mf("name.3mf", {"Metadata/plate_1.gcode": fx.tile_gcode().encode()})
        with open(p, "rb") as fh:
            data = bytearray(fh.read())
        cd = data.rindex(b"PK\x01\x02")
        data[cd + 9] |= 0x08                                    # general purpose flag bit 11: name is UTF-8
        data[cd + 46] = 0xFF                                    # first byte of the name
        p = self.write("name.3mf", bytes(data))
        with self.assertRaisesRegex(check_gcode.InputError, "not a readable 3mf"):
            list(check_gcode.read_gcode(p))
        rc, out, err = fx.run("check_gcode.py", p, self.write("f.txt", fx.FEATURES))
        self.assertEqual(rc, 2, err)
        self.assertNotIn("Traceback", err)

    def test_3mf_cap(self):
        self.assertEqual(check_gcode.MAX_3MF_BYTES, 64 * fx.MB)


class RealArchivesStillOpen(fx.TempDir):
    def test_normal_3mf_passes_precheck(self):
        p = self.gcode_3mf("t.3mf", {"Metadata/plate_1.gcode": fx.tile_gcode().encode()})
        check_gcode.check_zip_directory(p)
        self.assertTrue(any(l.startswith("; Z_HEIGHT") for l in check_gcode.read_gcode(p)))

    def test_zip64_3mf_passes_precheck(self):
        p = self.gcode_3mf("t.3mf", {"Metadata/plate_1.gcode": iter([fx.tile_gcode().encode()])})   # force_zip64
        check_gcode.check_zip_directory(p)
        self.assertTrue(any(l.startswith("; Z_HEIGHT") for l in check_gcode.read_gcode(p)))


if __name__ == "__main__":
    unittest.main()
