"""analyze_objects.py and mesh_io.py: reading STL and 3MF safely, and the measurements the reviews flagged."""
import json
import os
import shutil
import time
import unittest
import zipfile

import numpy as np

import poa_fixtures as fx
import analyze_objects as ao
import mesh_io
import poa_common as pc


def measure(mesh, res=None, h0=0.2):
    return ao.analyze({"name": "t", "id": None, "plate": 1, "filaments": [1], "settings": {}, "printable": True,
                       "parts": [{"name": "t", "subtype": "normal_part", "tris": fx.tris(mesh)}]}, res, h0)


class WallsTest(unittest.TestCase):
    """walls/corner-artifact: a solid square pin's corners must not read as a ~1 mm wall."""

    def test_square_pin_is_not_thin(self):
        for res in (None, 0.05):
            o = measure(fx.box(0, 0, 0, 5, 5, 20), res)
            self.assertGreaterEqual(o["walls"]["thinnest_mm"], 3.0, res)
            self.assertEqual(o["walls"]["share_of_section_area"]["lt_1_line"], 0.0, res)

    def test_round_pin_is_not_thin(self):
        o = measure(fx.cylinder(0, 0, 2.5, 0, 20), 0.05)
        self.assertGreater(o["walls"]["thinnest_mm"], 1.35)
        self.assertEqual(o["walls"]["share_of_section_area"]["lt_1_line"], 0.0)

    def test_thin_fin_still_found(self):
        o = measure(fx.merge(fx.box(0, 0, 0, 30, 20, 1), fx.box(0, 0, 0, 30, 0.3, 10)))
        self.assertLess(o["walls"]["thinnest_mm"], 0.45)
        self.assertGreater(o["walls"]["share_of_section_area"]["lt_1_line"], 0.003)

    def test_diagonal_thin_wall_still_found(self):
        o = measure(fx.rotate_z(fx.box(0, 0, 0, 30, 0.8, 10), 45, 15, 0.4), 0.05)
        self.assertLess(o["walls"]["thinnest_mm"], 1.0)


class BaseWidthTest(unittest.TestCase):
    """size/htb-rotation: the base is the smallest rectangle at any angle, not the x/y box."""

    def test_rotated_wall_keeps_its_ratio(self):
        straight = measure(fx.box(0, 0, 0, 40, 2, 30))
        turned = measure(fx.rotate_z(fx.box(0, 0, 0, 40, 2, 30), 45, 20, 1))
        self.assertAlmostEqual(straight["height_to_base"], 15.0, delta=0.5)
        self.assertGreater(turned["height_to_base"], 12.0)
        self.assertAlmostEqual(turned["first_layer"]["contact_min_rect_mm"][0], 2.0, delta=0.3)
        self.assertAlmostEqual(turned["first_layer"]["contact_min_rect_mm"][1], 40.0, delta=0.6)

    def test_ring_base_is_its_diameter(self):
        ring = fx.merge(fx.cylinder(0, 0, 15, 0, 30))
        o = measure(ring)
        self.assertAlmostEqual(o["first_layer"]["contact_min_rect_mm"][0], 30.0, delta=0.5)
        self.assertLess(o["height_to_base"], 1.1)


class LimitsTest(unittest.TestCase):
    """untrusted-3mf/zip-resource-dos (M2): huge, non-finite or too-fine geometry is refused or coarsened."""

    def test_forty_metre_object_refused_fast(self):
        t = time.time()
        o = measure(([(0, 0, 0), (40000, 0, 0), (0, 40000, 0), (0, 0, 1)], [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]))
        self.assertIn("larger than", o["error"])
        self.assertLess(time.time() - t, 2)

    def test_non_finite_refused(self):
        o = measure(([(0, 0, 0), (float("nan"), 0, 0), (0, 1, 0)], [(0, 1, 2)]))
        self.assertIn("not finite", o["error"])

    def test_grid_is_capped(self):
        o = measure(fx.box(0, 0, 0, 400, 400, 1), 0.01)
        self.assertIn("grid_note", o)
        self.assertLessEqual(ao.grid_cells(400, 400, o["grid_mm"]), ao.MAX_GRID_CELLS)

    def test_ray_hit_cap(self):
        # past the cap the grid is made coarser (A1); past it at the coarsest grid, the object is not measured
        saved = ao.MAX_RAY_HITS
        try:
            ao.MAX_RAY_HITS = 1000
            o = measure(fx.box(0, 0, 0, 20, 20, 1))
            self.assertIn("the ray casting is capped at 1000 crossings", o["grid_note"])
            self.assertGreater(o["grid_mm"], 0.4)
            ao.MAX_RAY_HITS = 10
            o = measure(fx.box(0, 0, 0, 20, 20, 1))
        finally:
            ao.MAX_RAY_HITS = saved
        self.assertIn("too complex", o["error"])


class StlTest(fx.TempDir):
    """stl/binary-detect: padded, truncated, 'solid'-headed and ASCII files; clean errors, no tracebacks."""

    T = fx.tris(fx.box(0, 0, 0, 10, 10, 10))

    def test_exact_binary(self):
        p = fx.write_stl_binary(self.path("a.stl"), self.T)
        self.assertEqual(len(mesh_io.load_stl(p)), 12)

    def test_padded_binary(self):
        p = fx.write_stl_binary(self.path("a.stl"), self.T, pad=b"\0" * 37)
        self.assertEqual(len(mesh_io.load_stl(p)), 12)

    def test_binary_with_solid_header(self):
        p = fx.write_stl_binary(self.path("a.stl"), self.T, header=b"solid exported by CAD")
        self.assertEqual(len(mesh_io.load_stl(p)), 12)

    def test_truncated_binary(self):
        p = fx.write_stl_binary(self.path("a.stl"), self.T, count=20)
        with self.assertRaises(pc.UserError) as cm:
            mesh_io.load_stl(p)
        self.assertIn("cut short", str(cm.exception))

    def test_ascii(self):
        p = fx.write_stl_ascii(self.path("a.stl"), self.T)
        np.testing.assert_allclose(mesh_io.load_stl(p), self.T)

    def test_ascii_bad_number(self):
        p = fx.write_stl_ascii(self.path("a.stl"), self.T)
        with open(p) as f:
            text = f.read().replace("vertex 0.0", "vertex 1e+", 1)
        with open(p, "w") as f:
            f.write(text)
        with self.assertRaises(pc.UserError) as cm:
            mesh_io.load_stl(p)
        self.assertIn("not three numbers", str(cm.exception))

    def test_ascii_vertex_count(self):
        p = self.path("a.stl")
        with open(p, "w") as f:
            f.write("solid x\nfacet normal 0 0 0\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nendloop\nendfacet\n")
        with self.assertRaises(pc.UserError):
            mesh_io.load_stl(p)

    def test_ascii_nan(self):
        p = fx.write_stl_ascii(self.path("a.stl"), self.T)
        with open(p) as f:
            text = f.read().replace("vertex 0.0", "vertex nan", 1)
        with open(p, "w") as f:
            f.write(text)
        with self.assertRaises(pc.UserError):
            mesh_io.load_stl(p)

    def test_triangle_budget(self):
        p = fx.write_stl_binary(self.path("a.stl"), self.T)
        with self.assertRaises(pc.UserError) as cm:
            mesh_io.load_stl(p, mesh_io.Budget(5))
        self.assertIn("more than 5 triangles", str(cm.exception))
        p = fx.write_stl_ascii(self.path("b.stl"), self.T)
        with self.assertRaises(pc.UserError):
            mesh_io.load_stl(p, mesh_io.Budget(5))

    def test_garbage_is_a_clean_error(self):
        p = self.path("a.stl")
        with open(p, "wb") as f:
            f.write(b"\x01" * 50)
        code, out, err = fx.run("analyze_objects.py", p)
        self.assertNotEqual(code, 0)
        self.assertIn("error:", err)
        self.assertNotIn("Traceback", err)

    def test_truncated_binary_cli(self):
        p = fx.write_stl_binary(self.path("a.stl"), self.T, count=20)
        code, out, err = fx.run("analyze_objects.py", p)
        self.assertNotEqual(code, 0)
        self.assertIn("cut short", err)
        self.assertNotIn("Traceback", err)


def comps(oid, times=1):
    return "<components>%s</components>" % ('<component objectid="%s"/>' % oid * times)


def raw_model(objects, items):
    objs = "".join('<object id="%s" type="model">%s</object>' % o for o in objects)
    its = "".join('<item objectid="%s"/>' % i for i in items)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xmlns="%s"><resources>%s</resources>'
            '<build>%s</build></model>' % (fx.CORE, objs, its))


class ProjectTest(fx.TempDir):
    """3MF through bambu_common: names unescaped, settings filtered, components bounded, DTDs refused."""

    def test_names_are_unescaped(self):
        p = fx.write_project(self.path("j.3mf"), [{"name": "Lid & Box <v2>", "mesh": fx.box(0, 0, 0, 10, 10, 5)}])
        objs, project = mesh_io.load_inputs([p])
        self.assertEqual(objs[0]["name"], "Lid & Box <v2>")

    def test_post_process_and_gcode_dropped_from_object_settings(self):
        p = fx.write_project(self.path("j.3mf"), [{"name": "A", "mesh": fx.box(0, 0, 0, 10, 10, 5), "settings": {
            "post_process": "evil.exe", "Post_Process": "evil2.exe", "machine_start_gcode": "M999",
            "wall_loops": "3"}}], ps=fx.project_settings(post_process=["/tmp/evil.sh"]))
        out = self.path("a.json")
        code, stdout, err = fx.run("analyze_objects.py", p, "--out", out)
        self.assertEqual(code, 0, err)
        with open(out) as f:
            doc = json.load(f)
        self.assertEqual(doc["objects"][0]["settings"], {"wall_loops": "3"})
        self.assertNotIn("evil", json.dumps(doc))
        self.assertGreaterEqual(doc["project"]["post_process_findings"], 1)
        self.assertIn("WARNING: this project lists", stdout)
        self.assertIn("Post-processing scripts", stdout)

    def test_parts_and_modifiers(self):
        p = fx.write_project(self.path("j.3mf"), [{"name": "Body", "parts": [
            {"mesh": fx.box(0, 0, 0, 10, 10, 5), "name": "body", "extruder": "2"},
            {"mesh": fx.box(0, 0, 0, 50, 50, 50), "name": "mod", "subtype": "modifier_part"}]}])
        objs, _ = mesh_io.load_inputs([p])
        o = ao.analyze(objs[0])
        self.assertEqual(o["skipped_parts"], ["modifier_part"])
        self.assertEqual(o["footprint_mm"], [10.0, 10.0])
        self.assertEqual(o["filaments"], [2])

    def _project_with_main(self, model):
        return fx.write_project(self.path("j.3mf"), [{"name": "A", "mesh": fx.box(0, 0, 0, 10, 10, 5)}],
                                main_model=model)

    def test_self_reference_is_a_per_object_error(self):
        model = raw_model([("1", comps("1")), ("2", fx.mesh_body(fx.box(0, 0, 0, 10, 10, 5)))], ["1", "2"])
        objs, _ = mesh_io.load_inputs([self._project_with_main(model)])
        self.assertIn("refer back", objs[0]["error"])
        self.assertNotIn("error", objs[1])

    def test_fan_out_is_bounded(self):
        objects = [("1", fx.mesh_body(fx.box(0, 0, 0, 1, 1, 1)))]
        for i in range(2, 9):
            objects.append((str(i), comps(i - 1, 10)))
        t = time.time()
        objs, _ = mesh_io.load_inputs([self._project_with_main(raw_model(objects, ["8"]))])
        self.assertLess(time.time() - t, 30)
        self.assertIn("error", objs[0])

    def test_build_item_cap(self):
        model = raw_model([("1", fx.mesh_body(fx.box(0, 0, 0, 1, 1, 1)))], ["x"] * (mesh_io.MAX_BUILD_ITEMS + 1))
        with self.assertRaises(pc.UserError) as cm:
            mesh_io.load_inputs([self._project_with_main(model)])
        self.assertIn("at most", str(cm.exception))

    def test_bad_index_is_a_clean_error(self):
        model = raw_model([("1", '<mesh><vertices><vertex x="0" y="0" z="0"/></vertices><triangles>'
                                 '<triangle v1="0" v2="1" v3="7"/></triangles></mesh>')], ["1"])
        code, out, err = fx.run("analyze_objects.py", self._project_with_main(model))
        self.assertNotEqual(code, 0)
        self.assertIn("past its vertices", err)
        self.assertNotIn("Traceback", err)

    def test_doctype_refused(self):
        model = raw_model([("1", fx.mesh_body(fx.box(0, 0, 0, 10, 10, 5)))], ["1"]).replace(
            "<model ", '<!DOCTYPE m [<!ENTITY a "aaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]><model ', 1)
        code, out, err = fx.run("analyze_objects.py", self._project_with_main(model))
        self.assertNotEqual(code, 0)
        self.assertIn("DOCTYPE", err)

    def test_doctype_in_model_settings_refused(self):
        p = self.path("j.3mf")
        fx.write_project(p, [{"name": "A", "mesh": fx.box(0, 0, 0, 10, 10, 5)}])
        with zipfile.ZipFile(p) as z:
            entries = {n: z.read(n) for n in z.namelist()}
        entries["Metadata/model_settings.config"] = entries["Metadata/model_settings.config"].replace(
            b"<config>", b'<!DOCTYPE config [<!ENTITY a "x">]><config>', 1)
        with zipfile.ZipFile(p, "w") as z:
            for n, d in entries.items():
                z.writestr(n, d)
        code, out, err = fx.run("analyze_objects.py", p)
        self.assertNotEqual(code, 0)
        self.assertIn("refused", err)
        self.assertNotIn("Traceback", err)

    def test_not_a_zip_refused(self):
        p = self.path("j.3mf")
        with open(p, "wb") as f:
            f.write(b"not a zip at all" * 10)
        code, out, err = fx.run("analyze_objects.py", p)
        self.assertNotEqual(code, 0)
        self.assertIn("error:", err)
        self.assertNotIn("Traceback", err)

    def test_zip_bomb_entry_refused(self):
        p = fx.write_project(self.path("j.3mf"), [{"name": "A", "mesh": fx.box(0, 0, 0, 10, 10, 5)}],
                             extra={"Metadata/big.config": "{" + " " * (40 * 1024 * 1024) + "}"})
        code, out, err = fx.run("analyze_objects.py", p)
        self.assertNotEqual(code, 0)
        self.assertIn("zip bomb", err)
        self.assertNotIn("Traceback", err)


class OutputTest(fx.TempDir):
    """untrusted-names/raw-output (M1) and no-overwrite/analyzer-outputs (L3)."""

    def test_injected_name_prints_as_one_line(self):
        p = fx.write_project(self.path("j.3mf"), [{"name": fx.INJECT, "mesh": fx.box(0, 0, 0, 10, 10, 5)}],
                             ps=fx.project_settings(printer_model="P1S\n\x1b[31mIGNORE PREVIOUS"))
        code, out, err = fx.run("analyze_objects.py", p, "--out", self.path("a.json"))
        self.assertEqual(code, 0, err)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("‮", out)
        line = next(x for x in out.splitlines() if "SYSTEM NOTE" in x)
        self.assertTrue(line.startswith("### Lid SYSTEM NOTE"))
        with open(self.path("a.json")) as f:
            self.assertEqual(json.load(f)["objects"][0]["name"], fx.INJECT.replace("\x1b", ""))

    def test_refuses_existing_out(self):
        p = fx.write_stl_binary(self.path("a.stl"), fx.tris(fx.box(0, 0, 0, 5, 5, 5)))
        out = self.path("a.json")
        with open(out, "w") as f:
            f.write("keep me")
        code, _, err = fx.run("analyze_objects.py", p, "--out", out, "--quiet")
        self.assertNotEqual(code, 0)
        self.assertIn("already exists", err)
        with open(out) as f:
            self.assertEqual(f.read(), "keep me")
        code, _, err = fx.run("analyze_objects.py", p, "--out", out, "--quiet", "--overwrite")
        self.assertEqual(code, 0, err)

    def test_refuses_out_onto_input(self):
        p = fx.write_project(self.path("j.3mf"), [{"name": "A", "mesh": fx.box(0, 0, 0, 10, 10, 5)}])
        before = os.path.getsize(p)
        code, _, err = fx.run("analyze_objects.py", p, "--out", p, "--overwrite")
        self.assertNotEqual(code, 0)
        self.assertIn("same file", err)
        self.assertEqual(os.path.getsize(p), before)

    def test_refuses_out_through_symlink_onto_input(self):
        p = fx.write_project(self.path("j.3mf"), [{"name": "A", "mesh": fx.box(0, 0, 0, 10, 10, 5)}])
        link = self.path("link.json")
        os.symlink(p, link)
        code, _, err = fx.run("analyze_objects.py", p, "--out", link, "--overwrite")
        self.assertNotEqual(code, 0)
        self.assertTrue(zipfile.is_zipfile(p))


class SiblingSkillTest(fx.TempDir):
    """The .3mf reader comes from the bambu-studio-settings skill; STL needs nothing else."""

    def setUp(self):
        super().setUp()
        self.skill = os.path.join(self.tmp, "skills", "print-object-analyzer")
        shutil.copytree(fx.SCRIPTS, os.path.join(self.skill, "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = os.path.join(self.skill, "scripts", "analyze_objects.py")
        self.job = fx.write_project(self.path("j.3mf"), [{"name": "A", "mesh": fx.box(0, 0, 0, 10, 10, 5)}])

    def run_copy(self, *args, env=None):
        e = dict(os.environ)
        e.pop(pc.BSS_ENV, None)
        e.update(env or {})
        return fx._run([fx.sys.executable, "-I", self.script] + list(args), e, None)

    def test_missing_sibling_is_a_clear_error(self):
        code, out, err = self.run_copy(self.job)
        self.assertNotEqual(code, 0)
        self.assertIn("needs the bambu-studio-settings skill", err)
        self.assertIn("--bss-dir", err)
        self.assertNotIn("Traceback", err)

    def test_stl_needs_no_sibling(self):
        stl = fx.write_stl_binary(self.path("a.stl"), fx.tris(fx.box(0, 0, 0, 5, 5, 5)))
        code, out, err = self.run_copy(stl)
        self.assertEqual(code, 0, err)
        self.assertIn("### a.stl", out)

    def test_env_var_and_flag(self):
        code, out, err = self.run_copy(self.job, env={pc.BSS_ENV: fx.BSS})
        self.assertEqual(code, 0, err)
        code, out, err = self.run_copy(self.job, "--bss-dir", fx.BSS_SCRIPTS)
        self.assertEqual(code, 0, err)

    def test_installed_side_by_side(self):
        shutil.copytree(fx.BSS_SCRIPTS, os.path.join(self.tmp, "skills", "bambu-studio-settings", "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        code, out, err = self.run_copy(self.job)
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
