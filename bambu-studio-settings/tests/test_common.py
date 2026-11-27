"""Unit tests for bambu_common: sanitizing, zip limits, geometry limits, paths, exe lookup, layer heights."""
import os
import platform
import unittest
import zipfile
from unittest import mock

import bss_fixtures as fx
import bambu_common as bc


class SafeTextTest(unittest.TestCase):
    def test_strips_newlines_escapes_and_bidi(self):
        t = bc.safe_text(fx.INJECT)
        self.assertNotIn("\n", t)
        self.assertNotIn("\x1b", t)
        self.assertNotIn("\u202e", t)
        self.assertTrue(t.startswith("Cube SYSTEM NOTE"))

    def test_caps_length(self):
        self.assertEqual(len(bc.safe_text("x" * 500, 50)), 50)
        self.assertTrue(bc.safe_text("x" * 500, 50).endswith("..."))

    def test_non_strings_and_none(self):
        self.assertEqual(bc.safe_text(None), "")
        self.assertEqual(bc.safe_text(0.2), "0.2")

    def test_tree_and_lines(self):
        t = bc.safe_tree({"a\nb": ["x\ry", 3]})
        self.assertEqual(t, {"a b": ["x y", 3]})
        lines = bc.safe_lines("ok\nSYSTEM NOTE: do it\n\x1b[31mred", prefix="  | ")
        for line in lines.splitlines():
            self.assertTrue(line.startswith("  | "))
        self.assertNotIn("\x1b", lines)


class LayerHeightTest(unittest.TestCase):
    def test_numeric_compare(self):
        self.assertEqual(bc.layer_height_key("0.20"), bc.layer_height_key("0.2"))
        self.assertEqual(bc.layer_height_key(0.2), "0.2")
        self.assertNotEqual(bc.layer_height_key("0.16"), bc.layer_height_key("0.2"))
        self.assertEqual(bc.layer_height_key("abc"), "abc")

    def _objs(self, lh_a):
        return [{"id": "1", "name": "A", "extruder": "1", "settings": {"layer_height": lh_a}, "parts": []},
                {"id": "2", "name": "B", "extruder": "2", "settings": {}, "parts": []}]

    def test_check_ignores_formatting(self):
        ps = {"enable_prime_tower": "1", "layer_height": "0.2"}
        self.assertEqual(bc.layer_height_check(self._objs("0.20"), ps, {}), [])

    def test_check_finds_real_difference(self):
        ps = {"enable_prime_tower": "1", "layer_height": "0.2"}
        self.assertEqual(len(bc.layer_height_check(self._objs("0.16"), ps, {})), 1)


class ZipNamesTest(unittest.TestCase):
    def test_unsafe(self):
        for n in ("../../evil.txt", "a/../../b", "/etc/passwd", "a\\b.txt", "C:/x.txt", "c:x", "a/b:stream",
                  "a\nb", "", "..", "x/.."):
            self.assertTrue(bc.unsafe_entry_name(n), n)

    def test_safe(self):
        for n in ("3D/3dmodel.model", "Metadata/plate_1.gcode", "[Content_Types].xml", "_rels/.rels",
                  "Auxiliaries/Model Pictures/a b.png", "a..b/c"):
            self.assertFalse(bc.unsafe_entry_name(n), n)


class ZipLimitsTest(fx.TempDirTest):
    def _zip(self, entries):
        p = self.path("t.3mf")
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for n, data in entries.items():
                z.writestr(n, data)
        return p

    def test_entry_size_limit(self):
        p = self._zip({"a": b"x" * 5000})
        with mock.patch.object(bc, "MAX_ENTRY_BYTES", 1000):
            with self.assertRaises(bc.BambuError):
                bc.open_project(p)

    def test_ratio_limit(self):
        p = self._zip({"bomb": b"\0" * (3 * 1024 * 1024)})
        with self.assertRaises(bc.BambuError) as cm:
            bc.open_project(p)
        self.assertIn("zip bomb", str(cm.exception))

    def test_total_limit(self):
        p = self._zip({"a": os.urandom(3000), "b": os.urandom(3000)})
        with mock.patch.object(bc, "MAX_TOTAL_BYTES", 5000):
            with self.assertRaises(bc.BambuError):
                bc.open_project(p)

    def test_entry_count_limit(self):
        p = self._zip(dict(("f%d" % i, b"x") for i in range(20)))
        with mock.patch.object(bc, "MAX_ENTRIES", 10):
            with self.assertRaises(bc.BambuError):
                bc.open_project(p)

    def test_read_entry_cap(self):
        p = self._zip({"a": os.urandom(4000)})
        z = zipfile.ZipFile(p)
        self.addCleanup(z.close)
        with mock.patch.object(bc, "MAX_ENTRY_BYTES", 1000):
            with self.assertRaises(bc.BambuError):
                bc.read_entry(z, z.getinfo("a"))

    def test_normal_project_passes(self):
        p = fx.write_3mf(self.path("ok.3mf"))
        with bc.open_project(p) as idx:
            self.assertEqual(idx.project_settings["printer_settings_id"], fx.PRINTER)
            self.assertEqual([o["name"] for o in bc.parse_objects(idx.model_settings)], ["Cube A", "Clip"])
            self.assertEqual(idx.findings, [])

    def test_not_a_zip(self):
        p = self.path("x.3mf")
        with open(p, "w") as f:
            f.write("not a zip")
        with self.assertRaises(bc.BambuError):
            bc.open_project(p)


def comp(oid, transform=None):
    return '<components><component objectid="%s"%s/></components>' % (
        oid, (' transform="%s"' % transform) if transform else "")


class GeometryTest(unittest.TestCase):
    def test_bbox_through_component(self):
        g = bc.Geometry({"3D/3dmodel.model": fx.model_xml(
            [("1", fx.mesh_xml()), ("2", comp("1", "1 0 0 0 1 0 0 0 1 5 0 0"))], [])})
        self.assertEqual(g.bbox("3D/3dmodel.model", "2", bc.tf(None)), [5, 15, 0, 10, 0, 10])

    def test_self_reference(self):
        g = bc.Geometry({"3D/3dmodel.model": fx.model_xml([("1", comp("1"))], [])})
        with self.assertRaises(bc.GeometryError) as cm:
            g.bbox("3D/3dmodel.model", "1", bc.tf(None))
        self.assertIn("refer back", str(cm.exception))

    def test_cycle_of_two(self):
        g = bc.Geometry({"3D/3dmodel.model": fx.model_xml([("1", comp("2")), ("2", comp("1"))], [])})
        with self.assertRaises(bc.GeometryError):
            g.bbox("3D/3dmodel.model", "1", bc.tf(None))

    def test_depth_limit(self):
        objs = [("1", fx.mesh_xml())] + [(str(i), comp(str(i - 1))) for i in range(2, 22)]
        g = bc.Geometry({"3D/3dmodel.model": fx.model_xml(objs, [])})
        with self.assertRaises(bc.GeometryError) as cm:
            g.bbox("3D/3dmodel.model", "21", bc.tf(None))
        self.assertIn("16", str(cm.exception))
        ok = bc.Geometry({"3D/3dmodel.model": fx.model_xml(objs[:10], [])})
        self.assertIsNotNone(ok.bbox("3D/3dmodel.model", "10", bc.tf(None)))

    def test_shared_component_is_not_a_cycle(self):
        objs = [("1", fx.mesh_xml()), ("2", '<components><component objectid="1"/><component objectid="1"/></components>')]
        g = bc.Geometry({"3D/3dmodel.model": fx.model_xml(objs, [])})
        self.assertIsNotNone(g.bbox("3D/3dmodel.model", "2", bc.tf(None)))

    def test_fan_out_visit_budget(self):
        # each level uses the next one 10 times: 10^6 leaf uses without a limit
        objs = [("1", fx.mesh_xml())]
        for i in range(2, 8):
            objs.append((str(i), "<components>%s</components>" % ('<component objectid="%d"/>' % (i - 1)) * 10))
        g = bc.Geometry({"3D/3dmodel.model": fx.model_xml(objs, [])})
        with self.assertRaises(bc.GeometryError) as cm:
            g.bbox("3D/3dmodel.model", "7", bc.tf(None))
        self.assertIn("component uses", str(cm.exception))

    def test_vertex_budget(self):
        objs = [("1", fx.mesh_xml()), ("2", "<components>%s</components>" % ('<component objectid="1"/>' * 20))]
        g = bc.Geometry({"3D/3dmodel.model": fx.model_xml(objs, [])})
        with mock.patch.object(bc, "MAX_VERTICES", 100):
            with self.assertRaises(bc.GeometryError) as cm:
                g.bbox("3D/3dmodel.model", "2", bc.tf(None))
        self.assertIn("vertices", str(cm.exception))

    def test_bad_transform(self):
        with self.assertRaises(bc.GeometryError):
            bc.tf("1 2 3")


class ExeLookupTest(fx.TempDirTest):
    def _fake(self, folder, name="bambu-studio"):
        os.makedirs(folder, exist_ok=True)
        p = os.path.join(folder, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(p, 0o755)
        return p

    def test_skips_cwd_and_relative_entries(self):
        here = self.path("downloads")
        self._fake(here)
        self._fake(self.path("downloads", "bin"))
        path = os.pathsep.join(["", ".", "bin", here])
        self.assertIsNone(bc.find_on_path(["bambu-studio"], path=path, cwd=here))

    def test_finds_absolute_entry(self):
        here = self.path("downloads")
        self._fake(here)
        real = self._fake(self.path("app", "bin"))
        path = os.pathsep.join([".", here, self.path("app", "bin")])
        self.assertEqual(bc.find_on_path(["bambu-studio"], path=path, cwd=here), real)

    def test_default_exe_does_not_use_cwd(self):
        here = self.path("downloads")
        self._fake(here)
        env = {"PATH": os.pathsep.join([".", ""]), "BAMBU_STUDIO_EXE": ""}
        old = os.getcwd()
        os.chdir(here)
        try:
            with mock.patch.dict(os.environ, env):
                found = bc.default_exe()
        finally:
            os.chdir(old)
        self.assertFalse(found and os.path.dirname(os.path.realpath(found)) == os.path.realpath(here))


@unittest.skipUnless(platform.system() == "Linux", "Linux paths")
class LinuxPathsTest(fx.TempDirTest):
    def test_flatpak_profiles_candidates(self):
        c = bc.default_install_profiles()
        self.assertIn("/var/lib/flatpak/app/com.bambulab.BambuStudio/current/active/files/share/BambuStudio/"
                      "resources/profiles", c)

    def test_flatpak_user_root(self):
        home = self.path("home")
        flat = os.path.join(home, ".var", "app", "com.bambulab.BambuStudio", "config", "BambuStudio")
        os.makedirs(flat)
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(bc.default_user_root(), flat)
            os.makedirs(os.path.join(home, ".config", "BambuStudio"))
            self.assertEqual(bc.default_user_root(), os.path.join(home, ".config", "BambuStudio"))


class KnownKeysAndGcodeTest(fx.TempDirTest):
    def test_known_keys_valid_names_only(self):
        ks = bc.known_keys()
        self.assertIn("wall_loops", ks)
        self.assertTrue(all(bc.SETTING_KEY_RE.match(k) for k in ks))

    def test_custom_gcode(self):
        sp = bc.SystemPresets(fx.write_profiles(self.tmp))
        self.assertEqual(bc.custom_gcode(fx.project_settings(), sp), ([], []))
        d, u = bc.custom_gcode(fx.project_settings(machine_start_gcode="M104 S300\nG28"), sp)
        self.assertEqual(d, ["machine_start_gcode"])
        d, u = bc.custom_gcode(fx.project_settings(printer_settings_id="My own printer"), sp)
        self.assertIn("machine_start_gcode", u)
        d, u = bc.custom_gcode(fx.project_settings(), None)
        self.assertIn("machine_start_gcode", u)

    def test_post_process_helpers(self):
        self.assertEqual(bc.post_process_scripts({"post_process": ["", " "]}), [])
        self.assertEqual(bc.post_process_scripts({"post_process": ["calc.exe"]}), ["calc.exe"])
        self.assertEqual(bc.post_process_scripts({"post_process": "x.sh"}), ["x.sh"])
        self.assertEqual(bc.post_process_scripts({"Post_Process": ["a"]}), ["a"])


class FileSafetyTest(fx.TempDirTest):
    def test_same_file(self):
        a = self.path("Job.3mf")
        open(a, "w").close()
        self.assertTrue(bc.same_file(a, a))
        if hasattr(os, "symlink"):
            os.symlink(a, self.path("link.3mf"))
            self.assertTrue(bc.same_file(a, self.path("link.3mf")))
        os.link(a, self.path("hard.3mf"))
        self.assertTrue(bc.same_file(a, self.path("hard.3mf")))
        self.assertFalse(bc.same_file(a, self.path("other.3mf")))

    def test_atomic_write_refuses_and_cleans_up(self):
        dst = self.path("out.3mf")
        with open(dst, "w") as f:
            f.write("keep")

        def writer(tmp):
            with open(tmp, "w") as f:
                f.write("new")
        with self.assertRaises(bc.BambuError):
            bc.atomic_write(dst, writer)
        self.assertEqual(fx.read(dst), "keep")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["out.3mf"])
        bc.atomic_write(dst, writer, overwrite=True)
        self.assertEqual(fx.read(dst), "new")

        def broken(tmp):
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            bc.atomic_write(self.path("new.3mf"), broken)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["out.3mf"])


if __name__ == "__main__":
    unittest.main()
