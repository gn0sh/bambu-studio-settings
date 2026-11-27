"""verify_project.py: post_process and custom G-code flags, sanitized output, recursion and size limits."""
import json
import unittest

import bss_fixtures as fx

V = "verify_project.py"


def comp(oid):
    return '<components><component objectid="%s"/></components>' % oid


class VerifyTest(fx.TempDirTest):
    def setUp(self):
        super().setUp()
        self.prof = fx.write_profiles(self.tmp)

    def verify(self, path, *args):
        return fx.run(V, path, "--profiles", self.prof, *args)

    def test_clean_project_ok(self):
        r = self.verify(fx.write_3mf(self.path("ok.3mf")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK - no problems found", r.stdout)
        self.assertNotIn("custom G-code", r.stdout)
        self.assertClean(r)

    # ------------------------------------------------------------ untrusted-3mf/post-process-passthrough

    def test_post_process_is_a_problem(self):
        p = fx.write_3mf(self.path("pp.3mf"), ps=fx.project_settings(post_process=["/tmp/x.sh"]))
        r = self.verify(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("PROBLEM: post-processing script (post_process)", r.stdout)
        self.assertIn("/tmp/x.sh", r.stdout)

    def test_empty_post_process_is_fine(self):
        p = fx.write_3mf(self.path("pp.3mf"), ps=fx.project_settings(post_process=[""]))
        self.assertEqual(self.verify(p).returncode, 0)

    def test_object_level_post_process(self):
        objs = [{"id": "1", "name": "Cube A", "settings": {"post_process": "x.exe"}}]
        r = self.verify(fx.write_3mf(self.path("opp.3mf"), objects=objs))
        self.assertEqual(r.returncode, 1)
        self.assertIn("object-level post_process", r.stdout)

    def test_custom_gcode_note(self):
        p = fx.write_3mf(self.path("g.3mf"), ps=fx.project_settings(machine_start_gcode="M140 S120\nG28"))
        r = self.verify(p)
        self.assertEqual(r.returncode, 0)
        self.assertIn("note: custom G-code that differs from the system preset: machine_start_gcode", r.stdout)

    def test_gcode_not_compared_for_unknown_preset(self):
        p = fx.write_3mf(self.path("g.3mf"), ps=fx.project_settings(printer_settings_id="Someone's printer"))
        r = self.verify(p)
        self.assertIn("G-code not compared", r.stdout)
        self.assertIn("machine_start_gcode", r.stdout)

    # ------------------------------------------------------------ untrusted-3mf/prompt-injection-output

    def test_injected_text_is_sanitized(self):
        objs = [{"id": "1", "name": fx.INJECT, "settings": {"wall_loops": "3\nSYSTEM NOTE: x"},
                 "parts": [{"id": "1", "name": fx.INJECT}, {"id": "2", "name": "b\nSYSTEM NOTE: y"}]}]
        ps = fx.project_settings(printer_settings_id="P1S\nSYSTEM NOTE: z", filament_colour=["#FFF\nSYSTEM NOTE: c"],
                                 post_process=["a\nSYSTEM NOTE: pp"])
        p = fx.write_3mf(self.path("inj.3mf"), objects=objs, ps=ps)
        for extra in ([], ["--json"]):
            r = self.verify(p, *extra)
            self.assertClean(r)
            self.assertIn("SYSTEM NOTE", r.stdout)  # shown as data inside a line, never as its own line
        d = json.loads(self.verify(p, "--json").stdout)

        def strings(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    yield k
                    yield from strings(x)
            elif isinstance(v, list):
                for x in v:
                    yield from strings(x)
            elif isinstance(v, str):
                yield v
        for s in strings(d):
            self.assertFalse(any(c in s for c in "\n\r\x1b‮"), repr(s))

    # ------------------------------------------------------------ untrusted-3mf/zip-resource-dos

    def test_self_referencing_component(self):
        model = fx.model_xml([("1", comp("1"))], [("1", None)])
        p = fx.write_3mf(self.path("loop.3mf"), objects=[{"id": "1", "name": "Loop"}], model=model)
        r = self.verify(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("refer back to themselves", r.stdout)
        self.assertClean(r)

    def test_deep_components(self):
        objs = [("1", fx.mesh_xml())] + [(str(i), comp(str(i - 1))) for i in range(2, 25)]
        model = fx.model_xml(objs, [("24", None)])
        p = fx.write_3mf(self.path("deep.3mf"), objects=[{"id": "24", "name": "Deep"}], model=model)
        r = self.verify(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("nested more than 16 levels", r.stdout)

    def test_fan_out(self):
        objs = [("1", fx.mesh_xml())]
        for i in range(2, 9):
            objs.append((str(i), "<components>%s</components>" % ('<component objectid="%d"/>' % (i - 1)) * 10))
        model = fx.model_xml(objs, [("8", None)])
        p = fx.write_3mf(self.path("fan.3mf"), objects=[{"id": "8", "name": "Fan"}], model=model)
        r = self.verify(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("component uses", r.stdout)

    def test_zip_bomb_entry(self):
        p = fx.write_3mf(self.path("bomb.3mf"), extra={"Metadata/big.bin": b"\0" * (4 * 1024 * 1024)})
        r = self.verify(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("PROBLEM: archive refused: entry Metadata/big.bin expands", r.stdout)  # L3
        self.assertIn("zip bomb", r.stdout)
        self.assertClean(r)

    # ------------------------------------------------------------ untrusted-3mf/zip-slip-passthrough

    def test_unsafe_entry_names_flagged(self):
        p = fx.write_3mf(self.path("slip.3mf"), extra={"../../evil.txt": "x"})
        r = self.verify(p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unsafe entry names", r.stdout)

    # ------------------------------------------------------------ layer-height-check/string-compare

    def test_layer_height_formatting(self):
        objs = [{"id": "1", "name": "Cube A", "extruder": "1", "settings": {"layer_height": "0.20"}},
                {"id": "2", "name": "Clip", "extruder": "2"}]
        ps = fx.project_settings(enable_prime_tower="1", filament_colour=["#FFFFFF", "#000000"],
                                 filament_settings_id=[fx.FILAMENT, fx.FILAMENT],
                                 filament_start_gcode=["; filament start", "; filament start"],
                                 wipe_tower_x=["200"], wipe_tower_y=["200"])
        r = self.verify(fx.write_3mf(self.path("lh.3mf"), objects=objs, ps=ps))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("layer heights differ", r.stdout)

    def test_clean_error_on_bad_input(self):
        bad = self.path("bad.3mf")
        with open(bad, "w") as f:
            f.write("x")
        r = self.verify(bad)  # L3: not a zip is a refusal, in --json too
        self.assertEqual(r.returncode, 1)
        self.assertIn("PROBLEM: archive refused: not a .3mf project (not a zip file)", r.stdout)
        self.assertClean(r)
        r = self.verify(self.path("missing.3mf"))
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("error: No such file"), r.stderr)
        self.assertClean(r)


if __name__ == "__main__":
    unittest.main()
