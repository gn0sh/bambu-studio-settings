"""Regression tests for the reviews at a9e255b (docs/reviews/skills-share-a9e255b.md): A1 ray casting bounds, A2
flat features, A3 ASCII STL memory, A4 markdown in report.md, A5 exact-case rules, recommend/same-name-pin,
walls/corner-artifact and B5 (\\Z in the key regex). Every fixture is generated here; none is committed."""
import json
import os
import struct
import sys
import time
import tracemalloc
import unittest

import numpy as np

import poa_fixtures as fx
import analyze_objects as ao
import mesh_io
import poa_common as pc
import recommend

sys.path.insert(0, fx.BSS_SCRIPTS)
import bambu_common as bc  # noqa: E402

MB = 2 ** 20


def obj(T, name="t"):
    return {"name": name, "id": None, "plate": 1, "filaments": [1], "settings": {}, "printable": True,
            "parts": [{"name": name, "subtype": "normal_part", "tris": T}]}


def measure(mesh, res=None):
    return ao.analyze(obj(fx.tris(mesh)), res, 0.2)


def big_flat_triangles(k, side=999.0):
    """The review's A1 mesh: k flat triangles stacked 0.5 mm apart, each over half of a side x side box."""
    T = []
    for i in range(k):
        z = i * 0.5
        a, b, c = (0, 0, z), (side, 0, z), (side, side, z)
        T.append((c, b, a) if i % 2 else (a, b, c))
    return np.array(T, dtype=np.float64)


def write_binary_stl(path, T):
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(T)))
        for t in T:
            f.write(struct.pack("<3f", 0, 0, 0) + struct.pack("<9f", *np.asarray(t, np.float32).ravel()) + b"\0\0")
    return path


class RayBoundsTest(fx.TempDir):
    """A1: real crossings are capped before they are stored (12 bytes each, int32/float32, one argsort); past a
    per-object cap the grid is made coarser; one run has a budget across its objects."""

    def test_review_stl_is_bounded(self):
        src = write_binary_stl(self.path("ray31.stl"), big_flat_triangles(31))
        self.assertLess(os.path.getsize(src), 2000)
        objs, _ = mesh_io.load_inputs([src])
        tracemalloc.start()
        t = time.perf_counter()
        try:
            o = ao.analyze(objs[0], None, 0.2)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 512 * MB)  # was 9.6 GB
        self.assertLess(time.perf_counter() - t, 120)
        self.assertNotIn("error", o)
        self.assertIn("the ray casting is capped at 12000000 crossings", o["grid_note"])
        self.assertGreater(o["grid_mm"], 0.4)

    def test_hits_are_capped_before_they_are_stored(self):
        T = fx.tris(fx.box(0, 0, 0, 20, 20, 1))
        saved = ao.MAX_RAY_HITS
        try:
            ao.MAX_RAY_HITS = 100
            with self.assertRaises(ao.RayLimit) as cm:
                ao.Columns(T, 0.1)
        finally:
            ao.MAX_RAY_HITS = saved
        self.assertIn("more than 100 times", str(cm.exception))
        cols = ao.Columns(T, 0.1)
        self.assertEqual(cols.cell.dtype, np.int64)
        self.assertAlmostEqual(float((cols.hi - cols.lo).sum()) * 0.01, 400.0, delta=12)

    def test_pairs_cap_coarsens_the_grid(self):
        saved = ao.MAX_RAY_PAIRS
        try:
            ao.MAX_RAY_PAIRS = 20000
            o = measure(fx.box(0, 0, 0, 20, 20, 5))
        finally:
            ao.MAX_RAY_PAIRS = saved
        self.assertIn("ray casting is capped", o["grid_note"])
        self.assertGreater(o["grid_mm"], 0.3)  # 4 flat triangles over 20 x 20 mm: 160,000 tests at 0.1 mm

    def test_run_budget_across_objects(self):
        rays = ao.RayBudget()
        T = fx.tris(fx.box(0, 0, 0, 20, 20, 5))
        saved = ao.MAX_RUN_RAY_HITS
        try:
            ao.MAX_RUN_RAY_HITS = 170000  # about 80,000 crossings each
            first = ao.analyze(obj(T, "a"), 0.1, 0.2, rays)
            second = ao.analyze(obj(T, "b"), 0.1, 0.2, rays)
            third = ao.analyze(obj(T, "c"), 0.1, 0.2, rays)
        finally:
            ao.MAX_RUN_RAY_HITS = saved
        self.assertNotIn("error", first)
        self.assertNotIn("error", second)
        self.assertIn("too complex to measure together", third["error"])

    def test_review_project_of_three(self):
        mesh = ([tuple(v) for t in big_flat_triangles(31) for v in t], [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(31)])
        src = fx.write_project(self.path("ray3.3mf"), [
            {"name": "o%d" % j, "mesh": mesh, "transform": "1 0 0 0 1 0 0 0 1 0 0 0"} for j in range(3)])
        t = time.perf_counter()
        code, out, err = fx.run("analyze_objects.py", src, "--out", self.path("a.json"), "--quiet")
        self.assertEqual(code, 0, err)
        self.assertLess(time.perf_counter() - t, 180)
        with open(self.path("a.json")) as f:
            objs = json.load(f)["objects"]
        self.assertEqual(len(objs), 3)
        self.assertIn("too complex to measure together", objs[2].get("error", ""))


class FlatFeaturesTest(unittest.TestCase):
    """A2: flat faces are grouped in one pass over their sorted heights, and the cross-sections added above them
    are capped (40k flat triangles took 116 s)."""

    def flat_stack(self, k, dz):
        T = []
        for i in range(k):
            z = i * dz
            a, b, c = (0, 0, z), (1, 0, z), (1, 1, z)
            T.append((a, b, c) if i % 2 else (c, b, a))
        return np.array(T, dtype=np.float64)

    def test_many_flat_levels(self):
        t = time.perf_counter()
        o = ao.analyze(obj(self.flat_stack(10000, 0.01)), None, 0.2)
        self.assertLess(time.perf_counter() - t, 60)
        self.assertEqual(o["z_features"]["count"], 10000 - 1)  # the face on the bed is not a feature
        self.assertLessEqual(o["small_layers"]["of"], 300 + ao.MAX_FEATURE_SLICES)

    def test_levels_within_0006_mm_join(self):
        o = measure(fx.merge(fx.box(0, 0, 0, 10, 10, 2), fx.box(20, 0, 0, 30, 10, 2.004), fx.box(40, 0, 0, 50, 10, 2.012)))
        zs = [lv["z"] for lv in o["z_features"]["levels"]]
        self.assertEqual(zs, [2.0, 2.012])
        self.assertAlmostEqual(o["z_features"]["levels"][0]["up_mm2"], 200.0, delta=0.01)


class AsciiStlTest(fx.TempDir):
    """A3: an ASCII STL is parsed a chunk at a time into one array (156 MB took 3.7 GB)."""

    def ascii(self, name, n):
        p = self.path(name)
        with open(p, "wb") as f:
            f.write(b"solid x\n")
            for i in range(n):
                f.write(b"facet normal 0 0 1\n outer loop\n  vertex %d 0 0\n  vertex 1 %d 0\n  vertex 0 0 1\n"
                        b" endloop\nendfacet\n" % (i % 100, i % 7))
            f.write(b"endsolid x\n")
        return p

    def test_memory_and_values(self):
        p = self.ascii("a.stl", 300000)
        size = os.path.getsize(p)
        tracemalloc.start()
        try:
            T = mesh_io.load_stl(p)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(T.shape, (300000, 3, 3))
        self.assertEqual(T[299999].tolist(), [[99, 0, 0], [1, 299999 % 7, 0], [0, 0, 1]])
        self.assertLess(peak, size + 300000 * 72 + 16 * MB)  # the file, the array and a chunk

    def test_too_many_vertices_refused_before_parsing(self):
        p = self.ascii("b.stl", 50000)
        with self.assertRaises(pc.UserError) as cm:
            mesh_io.load_stl(p, mesh_io.Budget(1000))
        self.assertIn("more than 1000 triangles", str(cm.exception))

    def test_bad_number_across_chunks(self):
        p = self.ascii("c.stl", 30000)
        with open(p, "ab") as f:
            f.write(b"facet\nvertex 1 2 x\nvertex 0 0 0\nvertex 0 0 0\nendfacet\n")
        with self.assertRaises(pc.UserError) as cm:
            mesh_io.load_stl(p)
        self.assertIn("not three numbers", str(cm.exception))


class ReportAndRulesTest(fx.TempDir):
    """A4, A5 and recommend/same-name-pin end to end: analyze -> recommend -> tune_project."""

    LINK = "![x](https://attacker.example/p.png) [click](https://attacker.example) <img src=x>"

    def run_all(self, objects, tested=None, hints=None):
        job = fx.write_project(self.path("job.3mf"), objects,
                               ps=fx.project_settings(enable_support="0", support_type="normal(auto)"))
        code, _, err = fx.run("analyze_objects.py", job, "--out", self.path("a.json"), "--quiet")
        self.assertEqual(code, 0, err)
        args = [self.path("a.json"), "--out", self.path("spec.json"), "--report", self.path("r.md")]
        for flag, table, name in (("--tested", tested, "tested.json"), ("--hints", hints, "hints.json")):
            if table:
                with open(self.path(name), "w") as f:
                    json.dump(table, f)
                args += [flag, self.path(name)]
        code, _, err = fx.run("recommend.py", *args)
        self.assertEqual(code, 0, err)
        with open(self.path("spec.json")) as f:
            spec = json.load(f)
        with open(self.path("r.md"), encoding="utf-8") as f:
            report = f.read()
        code, out, err = fx.run_bss("tune_project.py", job, self.path("tuned.3mf"), "--spec", self.path("spec.json"))
        self.assertEqual(code, 0, out + err)
        with bc.open_project(self.path("tuned.3mf")) as idx:
            tuned = dict((o["name"], o["settings"]) for o in reversed(bc.parse_objects(idx.model_settings)))
            plate = idx.project_settings
        return spec, report, tuned, plate, out

    def test_markdown_in_names_is_escaped(self):
        spec, report, _, _, _ = self.run_all([{"name": self.LINK, "mesh": fx.box(0, 0, 0, 20, 20, 10)}])
        self.assertNotIn("](", report.replace("\\](", ""))
        self.assertNotIn("<img", report.replace("\\<img", ""))
        self.assertIn("### \\!\\[x\\](https://attacker.example/p.png) \\[click\\](https://attacker.example) "
                      "\\<img src=x\\>", report)
        self.assertEqual(pc.md("a_b _c `d` *e* |f|"), "a_b \\_c \\`d\\` \\*e\\* \\|f\\|")

    def test_rules_match_the_exact_case(self):
        fin, blk = fx.box(0, 0, 0, 0.6, 20, 30), fx.box(0, 0, 0, 20, 20, 10)
        spec, report, tuned, _, _ = self.run_all(
            [{"name": "Lid", "mesh": blk}, {"name": "LID", "mesh": fin}],
            tested={"(?-i:^Lid\\Z)": {"wall_loops": "5", "why": "tested"}})
        for r in spec["objects"]:
            self.assertTrue(r["match"].startswith("(?-i:^") and r["match"].endswith("\\Z)"), r["match"])
        self.assertEqual(tuned["Lid"].get("wall_loops"), "5")
        self.assertNotEqual(tuned["LID"].get("wall_loops"), "5")  # was: LID got Lid's tested settings
        self.assertNotIn("differ only in letter case", report)

    def test_case_collision_is_flagged(self):
        blk = fx.box(0, 0, 0, 20, 20, 10)
        _, report, _, _, _ = self.run_all([{"name": "Lid", "mesh": blk}, {"name": "LID", "mesh": blk}],
                                          tested={"^Lid$": {"wall_loops": "5", "why": "tested"}})
        self.assertIn("objects LID, Lid differ only in letter case", report)

    def test_same_name_objects_that_need_different_settings(self):
        over = fx.merge(fx.box(0, 0, 0, 10, 10, 10), fx.box(0, 0, 10, 30, 10, 12))  # a 20 mm flat overhang
        spec, report, tuned, plate, _ = self.run_all([
            {"name": "Part", "mesh": over, "transform": "1 0 0 0 1 0 0 0 1 20 20 0"},
            {"name": "Part", "mesh": fx.box(0, 0, 0, 20, 20, 20), "transform": "1 0 0 0 1 0 0 0 1 80 20 0"},
            {"name": "Other", "mesh": over, "transform": "1 0 0 0 1 0 0 0 1 20 80 0"}])
        self.assertEqual(spec["plate"].get("enable_support"), "1")
        self.assertFalse([r for r in spec["objects"] if "Part" in r["match"]], spec["objects"])
        self.assertIn("2 objects are named Part but need different settings (enable_support", report)
        self.assertNotEqual(tuned["Part"].get("enable_support"), "0")  # was: the pin took the overhang's support
        with bc.open_project(self.path("tuned.3mf")) as idx:  # both "Part" objects, not the one the dict keeps
            parts = [o for o in bc.parse_objects(idx.model_settings) if o["name"] == "Part"]
        self.assertEqual(len(parts), 2)
        for o in parts:  # neither got a per-object pin or rule: both follow the plate
            self.assertNotIn(o["settings"].get("enable_support"), ("0",), parts)
        self.assertEqual(plate["enable_support"], "1")

    def test_unmeasured_object_without_id_with_an_analyzed_name(self):
        over = fx.merge(fx.box(0, 0, 0, 10, 10, 10), fx.box(0, 0, 10, 30, 10, 12))  # a 20 mm flat overhang
        job = fx.write_project(self.path("job.3mf"), [{"name": "Fin", "mesh": over, "transform": "1 0 0 0 1 0 0 0 1 20 20 0"}] +
                               [{"name": "Block %d" % i, "mesh": fx.box(0, 0, 0, 20, 20, 20),
                                 "transform": "1 0 0 0 1 0 0 0 1 %d 80 0" % (20 + 60 * i)} for i in range(2)],
                               ps=fx.project_settings(enable_support="0", support_type="normal(auto)"))
        code, _, err = fx.run("analyze_objects.py", job, "--out", self.path("a.json"), "--quiet")
        self.assertEqual(code, 0, err)
        with open(self.path("a.json")) as f:
            A = json.load(f)
        A["objects"].append({"name": "Fin", "id": None, "error": "no printing mesh"})
        with open(self.path("a.json"), "w") as f:
            json.dump(A, f)
        code, _, err = fx.run("recommend.py", self.path("a.json"), "--out", self.path("spec.json"),
                              "--report", self.path("r.md"))
        self.assertEqual(code, 0, err)
        with open(self.path("spec.json")) as f:
            spec = json.load(f)
        with open(self.path("r.md"), encoding="utf-8") as f:
            report = f.read()
        self.assertFalse([r for r in spec["objects"] if "Fin" in r["match"]], spec["objects"])
        self.assertIn("could not be measured and has no id", report)

    def test_same_name_objects_that_agree_share_one_rule(self):
        fin, blk = fx.box(0, 0, 0, 0.6, 20, 30), fx.box(0, 0, 0, 20, 20, 10)
        spec, _, tuned, _, _ = self.run_all([{"name": "Fin", "mesh": fin}, {"name": "Fin", "mesh": fin}] +
                                            [{"name": "Block %d" % i, "mesh": blk} for i in range(3)])
        fin_rules = [r for r in spec["objects"] if "Fin" in r["match"]]
        self.assertEqual(len(fin_rules), 1, spec["objects"])
        self.assertEqual(tuned["Fin"].get("wall_generator"), fin_rules[0]["set"].get("wall_generator"))


class WallsTest(unittest.TestCase):
    """walls/corner-artifact: square sections read their width (a 20 mm cube read 14.14, a 1.2 mm post 0.85)."""

    def test_tiny_post_triggers_w4(self):
        o = measure(fx.box(0, 0, 0, 0.3, 0.3, 10), None)
        self.assertIsNotNone(o["walls"]["thinnest_mm"])
        self.assertLess(o["walls"]["thinnest_mm"], 0.9)
        self.assertTrue(o["walls"]["thinnest_approximate"])
        r, _ = recommend.recommend_object(o, {}, {}, {}, 0.4)
        self.assertTrue(any("[W4]" in n for n in r.notes), (o["walls"], r.notes))

    def test_square_sections(self):
        for side, res in ((20, None), (1.2, None), (1.2, 0.05), (3, 0.1)):
            o = measure(fx.box(0, 0, 0, side, side, 10), res)
            self.assertAlmostEqual(o["walls"]["thinnest_mm"], side, delta=max(0.1, side * 0.02), msg=(side, res))
        o = measure(fx.box(0, 0, 0, 1.2, 1.2, 10))
        self.assertEqual(o["walls"]["share_of_section_area"]["1_to_2_lines"], 0.0)  # no false W4

    def test_turned_square_and_thin_tip(self):
        o = measure(fx.rotate_z(fx.box(0, 0, 0, 5, 5, 10), 45, 2.5, 2.5), 0.05)
        self.assertGreater(o["walls"]["thinnest_mm"], 4.8)
        L, t0, t1, h = 20, 3.0, 0.3, 10
        V = [(0, -t0 / 2, 0), (L, -t1 / 2, 0), (L, t1 / 2, 0), (0, t0 / 2, 0),
             (0, -t0 / 2, h), (L, -t1 / 2, h), (L, t1 / 2, h), (0, t0 / 2, h)]
        F = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
             (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
        o = measure((V, F), 0.05)
        self.assertLess(o["walls"]["thinnest_mm"], 0.7)
        self.assertGreater(o["walls"]["share_of_section_area"]["lt_1_line"], 0.0)


class KeyRegexTest(unittest.TestCase):
    """B5: setting keys end at \\Z in the analyzer too."""

    def test_trailing_newline(self):
        for rx in (mesh_io.SETTING_KEY, recommend.SETTING_KEY):
            self.assertTrue(rx.match("wall_loops"))
            self.assertFalse(rx.match("wall_loops\n"))
        self.assertEqual(mesh_io.object_settings({"wall_loops\n": "9", "wall_loops": "3"}), {"wall_loops": "3"})


if __name__ == "__main__":
    unittest.main()
