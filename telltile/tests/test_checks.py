"""check_stl.py and check_gcode.py on good input: the shipped tile, a cube, and a tiny synthetic sliced tile."""
import json
import os
import unittest

import ptt_fixtures as fx
import check_gcode
import check_stl


class Stl(fx.TempDir):
    def test_shipped_tile(self):
        r = check_stl.check(os.path.join(fx.SKILL, "assets", "tuning-tile_generic.stl"))
        self.assertEqual(r["size"], [112.0, 84.0, 10.6])
        self.assertEqual(r["bad_edges"], 0)

    def test_binary_cube(self):
        p = self.path("cube.stl"); fx.binary_stl(p, fx.cube_tris())
        r = check_stl.check(p)
        self.assertEqual((r["triangles"], r["bad_edges"], len(r["shells"]), r["volume_cm3"]), (12, 0, 1, 1.0))

    def test_binary_header_starting_with_solid(self):
        p = self.path("cube.stl"); fx.binary_stl(p, fx.cube_tris(), header=b"solid facet exported by some tool")
        self.assertEqual(check_stl.check(p)["triangles"], 12)

    def test_ascii_cube(self):
        lines = ["solid cube"]
        for t in fx.cube_tris():
            lines += ["facet normal 0 0 0", " outer loop"] + ["  vertex %g %g %g" % p for p in t] + [" endloop", "endfacet"]
        p = self.write("cube.stl", "\n".join(lines + ["endsolid cube", ""]))
        r = check_stl.check(p)
        self.assertEqual((r["triangles"], r["bad_edges"]), (12, 0))
        rc, out, err = fx.run("check_stl.py", p, "--expect-shells", "1")
        self.assertEqual(rc, 0, err)
        self.assertIn("-> OK", out)

    def test_bad_edges_counted_once(self):
        t = fx.cube_tris()
        p = self.path("open.stl"); fx.binary_stl(p, t[1:])                  # one triangle missing: a 3-edge hole
        self.assertEqual(check_stl.check(p)["bad_edges"], 3)
        p = self.path("dup.stl"); fx.binary_stl(p, t + t[:1])               # one triangle twice: 3 edges used 3 times
        self.assertEqual(check_stl.check(p)["bad_edges"], 3)

    def test_ascii_with_bom(self):
        lines = ["\ufeffsolid cube"]
        for t in fx.cube_tris():
            lines += ["facet normal 0 0 0", " outer loop"] + ["  vertex %g %g %g" % p for p in t] + [" endloop", "endfacet"]
        p = self.write("bom.stl", "\n".join(lines + ["endsolid cube", ""]))
        self.assertEqual(check_stl.check(p)["triangles"], 12)


class Gcode(fx.TempDir):
    def check(self, gcode=None, feats=fx.FEATURES, path=None):
        g = path or self.write("plate_1.gcode", gcode)
        return fx.run("check_gcode.py", g, self.write("features.txt", feats), "--json")

    def test_all_features_present(self):
        rc, out, err = self.check(fx.tile_gcode())
        self.assertEqual(rc, 0, out + err)
        r = json.loads(out)
        self.assertEqual(r["summary"]["failed"], 0)
        self.assertEqual(r["summary"]["first_layer_span_mm"], [112.0, 84.0])

    def test_missing_feature_fails(self):
        rc, out, err = self.check(fx.tile_gcode(with_top=False))
        self.assertEqual(rc, 1, err)
        self.assertEqual(json.loads(out)["summary"]["failed"], 1)

    def test_sliced_3mf(self):
        p = self.gcode_3mf("tile.gcode.3mf", {"3D/3dmodel.model": b"<model/>",
                                              "Metadata/plate_1.gcode": fx.tile_gcode().encode()})
        rc, out, err = self.check(path=p)
        self.assertEqual(rc, 0, out + err)

    def test_default_footprint_without_tile_line(self):
        feats = "\n".join(l for l in fx.FEATURES.splitlines() if "TILE" not in l) + "\n"
        rc, out, err = self.check(fx.tile_gcode(), feats)
        self.assertEqual(rc, 0, out + err)


class ShippedFeatureTable(unittest.TestCase):
    PATH = os.path.join(fx.SKILL, "assets", "features_generic.txt")

    def test_parses_and_matches_the_tile(self):
        w, d, feats = check_gcode.read_features(self.PATH)
        self.assertEqual((w, d), (112.0, 84.0))
        self.assertGreater(len(feats), 40)
        for f in feats:
            self.assertTrue(0 <= f["x"] <= w and 0 <= f["y"] <= d and 0 < f["z"] <= 10.6, f["name"])

    def test_one_feat_call_per_line(self):
        """The table lists what tile.scad echoes: as many FEAT lines as names in the file (loops give several)."""
        names = [f["name"] for f in check_gcode.read_features(self.PATH)[2]]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("boss 20mm (flat top)", names)

    def test_matches_the_shipped_stl(self):
        """Each top/tip/info feature's expected height is the STL's top surface within its search radius."""
        cells = {}                                       # 2 mm cells -> triangles whose x-y box touches them
        for t in check_stl.read_stl(os.path.join(fx.SKILL, "assets", "tuning-tile_generic.stl")):
            xs, ys = [p[0] for p in t], [p[1] for p in t]
            for i in range(int(min(xs) // 2), int(max(xs) // 2) + 1):
                for j in range(int(min(ys) // 2), int(max(ys) // 2) + 1):
                    cells.setdefault((i, j), []).append(t)

        def top_at(x, y):
            best = None
            for a, b, c in cells.get((int(x // 2), int(y // 2)), []):
                if not (min(a[0], b[0], c[0]) <= x <= max(a[0], b[0], c[0]) and min(a[1], b[1], c[1]) <= y <= max(a[1], b[1], c[1])):
                    continue
                den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
                if abs(den) < 1e-12:
                    continue
                l1 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / den
                l2 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / den
                if min(l1, l2, 1 - l1 - l2) >= -1e-9:
                    z = l1 * a[2] + l2 * b[2] + (1 - l1 - l2) * c[2]
                    best = z if best is None else max(best, z)
            return best

        w, d, feats = check_gcode.read_features(self.PATH)
        for f in feats:
            if f["kind"] == "bridge":
                continue
            lo = 0.8 if f["kind"] == "tip" else 0.25
            ok = lambda z: z is not None and -lo - 1e-6 <= z - f["z"] <= 0.25 + 1e-6   # noqa: B023 (used at once)
            if ok(top_at(f["x"], f["y"])):
                continue
            grid = [(f["x"] + f["r"] * i / 6, f["y"] + f["r"] * j / 6) for i in range(-6, 7) for j in range(-6, 7)
                    if i * i + j * j <= 36]
            zs = [z for z in (top_at(x, y) for x, y in grid) if z is not None]
            self.assertTrue(zs and ok(max(zs)), "%s: expected z %s, STL top within r %s" % (f["name"], f["z"], max(zs or [None])))

    def test_docs_point_to_it(self):
        self.assertIn("assets/features_generic.txt", fx.text("SKILL.md"))
        self.assertIn("assets/features_generic.txt", fx.text("references", "tile-spec.md"))

    def test_one_photo_list(self):
        self.assertNotIn("Three-quarter", fx.text("references", "reading-guide.md"))
        self.assertIn("photo-instructions.md", fx.text("references", "reading-guide.md"))


class Arcs(fx.TempDir):
    """G2/G3 are followed along the arc (arc fitting is on in Bambu's P1S/X1C presets)."""
    HEAD = ["; Z_HEIGHT: 0.2", "; FEATURE: Outer wall", "G1 X10 Y0"]

    def pts(self, *moves):
        return check_gcode.samples(self.HEAD + list(moves))

    def test_full_circle(self):
        p = self.pts("G2 X10 Y0 I-2 J0 E1")
        self.assertGreater(len(p), 25)
        for x, y, _, _ in p:
            self.assertAlmostEqual(((x - 8) ** 2 + y ** 2) ** 0.5, 2.0, places=6)
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        self.assertAlmostEqual(min(xs), 6.0, delta=0.05); self.assertAlmostEqual(max(ys), 2.0, delta=0.05)
        self.assertAlmostEqual(min(ys), -2.0, delta=0.05)

    def test_half_arcs_turn_the_right_way(self):
        up = self.pts("G3 X6 Y0 I-2 J0 E1")       # counter-clockwise from (10, 0) round (8, 0): through y = +2
        down = self.pts("G2 X6 Y0 I-2 J0 E1")     # clockwise: through y = -2
        self.assertAlmostEqual(max(q[1] for q in up), 2.0, delta=0.05); self.assertGreaterEqual(min(q[1] for q in up), -1e-9)
        self.assertAlmostEqual(min(q[1] for q in down), -2.0, delta=0.05); self.assertLessEqual(max(q[1] for q in down), 1e-9)
        self.assertEqual((round(up[-1][0], 6), round(up[-1][1], 6)), (6.0, 0.0))

    def test_r_form(self):
        self.assertAlmostEqual(min(q[1] for q in self.pts("G2 X6 Y0 R2 E1")), -2.0, delta=0.05)
        short, long_ = self.pts("G2 X8 Y2 R2 E1"), self.pts("G2 X8 Y2 R-2 E1")
        self.assertGreater(len(long_), 2 * len(short))       # R < 0 goes the long way (3/4 vs 1/4 circle)
        with self.assertRaisesRegex(check_gcode.InputError, "cannot reach"):
            self.pts("G2 X0 Y0 R2 E1")
        with self.assertRaisesRegex(check_gcode.InputError, "needs I/J or R"):
            self.pts("G2 X6 Y0 E1")

    def test_arc_length_counts_for_the_caps(self):
        with self.assertRaisesRegex(check_gcode.InputError, "one extruding move"):
            self.pts("G2 X10 Y0 I-70 J0 E1")                 # a 70 mm radius circle: 440 mm, chord 0
        fx.Patch(self, check_gcode, MAX_POINTS=20)
        with self.assertRaisesRegex(check_gcode.InputError, "sample points"):
            self.pts("G2 X10 Y0 I-2 J0 E1")

    def test_boss_found_through_a_full_circle(self):
        """The boss top printed as one G2 circle (r 2 round model (17, 50), passing 1 mm from the boss at (16, 50)).
        Read as a chord, the circle was one point 3 mm away and the boss FAILed as "nothing printed"."""
        g = fx.tile_gcode(with_top=False) + "; Z_HEIGHT: 6.0\n; FEATURE: Top surface\nG0 X%.3f Y%.3f\nG2 X%.3f Y%.3f I-2 J0 E0.3\n" % (
            50 + 19, 50 + 50, 50 + 19, 50 + 50)
        rc, out, err = fx.run("check_gcode.py", self.write("a.gcode", g), self.write("f.txt", fx.FEATURES), "--json")
        self.assertEqual(rc, 0, out + err)


if __name__ == "__main__":
    unittest.main()
