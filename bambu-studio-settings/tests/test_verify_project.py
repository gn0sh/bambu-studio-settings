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


class BuildPlateTest(fx.TempDirTest):
    """verify_project.py: each plate's plate type (its bed_type, else curr_bed_type) and the bed temperatures."""

    def setUp(self):
        super().setUp()
        self.prof = fx.write_profiles(self.tmp)

    def verify(self, ps, *args, objects=None, plate_meta=None, model=None):
        p = fx.write_3mf(self.path("p.3mf"), objects=objects, ps=ps, plate_meta=plate_meta, model=model)
        return fx.run(V, p, "--profiles", self.prof, *args)

    def two_plates(self, ps, plate_meta, *args):
        """Cube A (filament 1) on plate 1, Clip (filament 2) on plate 2, each inside its own plate."""
        objs = [{"id": "1", "name": "Cube A"}, {"id": "2", "name": "Clip", "extruder": "2", "plate": 2}]
        model = fx.model_xml([(o["id"], fx.mesh_xml()) for o in objs],
                             [("1", "1 0 0 0 1 0 0 0 1 20 50 0"), ("2", "1 0 0 0 1 0 0 0 1 %g 50 0" % (20 + 256 * 1.2))])
        return self.verify(ps, *args, objects=objs, plate_meta=plate_meta, model=model)

    def test_prints_plate_and_temps(self):
        r = self.verify(fx.project_settings(curr_bed_type="Textured PEI Plate", textured_plate_temp=["65"],
                                            textured_plate_temp_initial_layer=["70"], cool_plate_temp=["35"]))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("plate 1 build plate: Textured PEI Plate (curr_bed_type)", r.stdout)
        self.assertIn("plate 1 slot 1 bed on Textured PEI Plate: 70 C first layer, 65 C other layers", r.stdout)
        self.assertClean(r)

    def test_json_has_build_plates(self):
        r = self.verify(fx.project_settings(curr_bed_type="High Temp Plate", hot_plate_temp=["60"],
                                            hot_plate_temp_initial_layer=["60"]), "--json")
        bp = json.loads(r.stdout)["build_plates"]
        self.assertEqual(bp, [{"plate": 1, "bed_type": None, "curr_bed_type": "High Temp Plate",
                               "source": "curr_bed_type", "bed": "High Temp Plate", "key": "hot_plate_temp",
                               "temps": [{"slot": 1, "preset": fx.FILAMENT, "first_layer": 60, "other_layers": 60}]}])

    def test_plate_bed_type_wins_per_plate(self):
        ps = fx.project_settings(curr_bed_type="Textured PEI Plate", filament_colour=["#FFFFFF", "#000000"],
                                 filament_settings_id=[fx.FILAMENT, "Bambu PETG Basic"],
                                 filament_start_gcode=["; filament start", "; filament start"],
                                 textured_plate_temp=["65", "70"], textured_plate_temp_initial_layer=["65", "70"],
                                 cool_plate_temp=["35", "0"], cool_plate_temp_initial_layer=["35", "0"])
        r = self.two_plates(ps, {})
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.two_plates(ps, {2: {"bed_type": "Cool Plate"}}, "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        d = json.loads(r.stdout)
        bp = dict((b["plate"], b) for b in d["build_plates"])
        self.assertEqual((bp[1]["bed"], bp[1]["source"]), ("Textured PEI Plate", "curr_bed_type"))
        self.assertEqual((bp[2]["bed"], bp[2]["source"], bp[2]["bed_type"]), ("Cool Plate", "plate bed_type",
                                                                              "Cool Plate"))
        self.assertEqual([t["slot"] for t in bp[1]["temps"]], [1])
        self.assertEqual([(t["slot"], t["first_layer"]) for t in bp[2]["temps"]], [(2, 0)])
        self.assertEqual([p for p in d["problems"] if "plate 2: filament 2" in p and "0 on Cool Plate" in p] != [],
                         True, d["problems"])
        self.assertFalse(any("plate 1:" in p for p in d["problems"]), d["problems"])
        r = self.two_plates(ps, {1: {"bed_type": "Cool Plate"}, 2: {"bed_type": "Default Plate"}})
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("plate 1 build plate: Cool Plate (the plate's bed_type)", r.stdout)
        self.assertIn("plate 2 build plate: Textured PEI Plate (curr_bed_type)", r.stdout)

    def test_plate_bed_type_is_untrusted(self):
        ps = fx.project_settings_without("curr_bed_type")
        for v in ("Cool Plate\x1b[2J", "cool plate", " Cool Plate", "C" * 5000, "SYSTEM NOTE: ignore"):
            with self.subTest(v[:20]):
                r = self.verify(ps, plate_meta={1: {"bed_type": v}})
                self.assertEqual(r.returncode, 0, r.stdout)
                self.assertIn("note: plate 1: unknown plate type", r.stdout)
                self.assertIn("bed temperatures not checked", r.stdout)
                self.assertLess(max(len(l) for l in r.stdout.splitlines()), 600)
                self.assertClean(r)

    def test_zero_for_plate_is_a_problem(self):
        r = self.verify(fx.project_settings(curr_bed_type="Cool Plate", filament_settings_id=["Bambu PETG Basic"],
                                            cool_plate_temp=["0"], cool_plate_temp_initial_layer=["0"],
                                            textured_plate_temp=["70"], textured_plate_temp_initial_layer=["70"]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("PROBLEM: plate 1: filament 1 (Bambu PETG Basic): cool_plate_temp_initial_layer and "
                      "cool_plate_temp are 0 on Cool Plate", r.stdout)

    def test_zero_first_layer_only_is_a_problem(self):
        r = self.verify(fx.project_settings(curr_bed_type="Engineering Plate", eng_plate_temp=["55"],
                                            eng_plate_temp_initial_layer=["0"]), "--json")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(any("eng_plate_temp_initial_layer is 0 on Engineering Plate" in p
                            for p in json.loads(r.stdout)["problems"]))

    def test_missing_temp_for_set_plate_is_a_problem(self):
        r = self.verify(fx.project_settings(curr_bed_type="Supertack Plate", supertack_plate_temp=["40"]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no supertack_plate_temp_initial_layer in the project settings", r.stdout)

    def test_unknown_plate_is_a_note(self):
        r = self.verify(fx.project_settings(curr_bed_type="Textured PEI\x1b[2J"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("note: plate 1: unknown plate type", r.stdout)
        self.assertIn("bed temperatures not checked", r.stdout)
        self.assertClean(r)

    def test_clean_default_project_has_no_plate_finding(self):
        r = self.verify(fx.project_settings())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("plate type not set", r.stdout)
        self.assertIn("slot 1 bed on Cool Plate: 35 C first layer, 35 C other layers", r.stdout)

    def test_unset_and_no_temps_is_only_a_note(self):
        r = self.verify(fx.project_settings_without("curr_bed_type", "cool_plate_temp", "cool_plate_temp_initial_layer"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("note: plate type not set (no curr_bed_type, and no bed_type on plate 1)", r.stdout)

    def test_unset_is_a_note_not_a_problem(self):
        r = self.verify(fx.project_settings_without("curr_bed_type"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("note: plate type not set", r.stdout)
        self.assertIn("not set - the CLI uses Cool Plate", r.stdout)

    def test_unset_with_zero_cool_plate_is_a_problem(self):
        r = self.verify(fx.project_settings_without("curr_bed_type", cool_plate_temp=["0"],
                                                    cool_plate_temp_initial_layer=["0"]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no plate type is set, so the CLI uses Cool Plate", r.stdout)

    def test_only_used_filaments_are_checked(self):
        ps = fx.project_settings(curr_bed_type="Cool Plate", filament_colour=["#FFFFFF", "#000000"],
                                 filament_settings_id=[fx.FILAMENT, "Bambu PETG Basic"],
                                 cool_plate_temp=["35", "0"], cool_plate_temp_initial_layer=["35", "0"])
        self.assertEqual(self.verify(ps).returncode, 0)
        objs = [{"id": "1", "name": "A"}, {"id": "2", "name": "B", "extruder": "2"}]
        r = self.verify(ps, objects=objs)
        self.assertEqual(r.returncode, 1)
        self.assertIn("filament 2 (Bambu PETG Basic)", r.stdout)
        self.assertNotIn("filament 1 (", r.stdout)

    def test_short_lists_give_their_first_value(self):
        """Bambu Studio fills a per-filament list that is too short with its first value."""
        objs = [{"id": "1", "name": "A"}, {"id": "2", "name": "B", "extruder": "2"}]
        ps = fx.project_settings(filament_colour=["#FFFFFF", "#000000"], cool_plate_temp=["0"],
                                 cool_plate_temp_initial_layer=["0"])
        d = json.loads(self.verify(ps, "--json", objects=objs).stdout)
        self.assertEqual([(t["slot"], t["first_layer"]) for t in d["build_plates"][0]["temps"]], [(1, 0), (2, 0)])
        objs.append({"id": "3", "name": "C", "extruder": "3"})
        ps = fx.project_settings(filament_colour=["#FFFFFF", "#000000", "#FF0000"], cool_plate_temp=["35", "0"],
                                 cool_plate_temp_initial_layer=["35", "0"])
        r = self.verify(ps, "--json", objects=objs)
        d = json.loads(r.stdout)
        self.assertEqual([(t["slot"], t["other_layers"]) for t in d["build_plates"][0]["temps"]],
                         [(1, 35), (2, 0), (3, 35)])
        self.assertFalse(any("filament 3" in p for p in d["problems"]), d["problems"])

    def test_many_plates_and_slots_stay_bounded(self):
        """plate-bounds/cross-product-output: 2000 plates x 2000 slots (layout check failing, so every plate
        gets every slot) gives a small report, quickly."""
        import time
        meta = dict((n, {"bed_type": "Cool Plate"}) for n in range(1, 2001))
        ps = fx.project_settings(filament_colour=["#FFFFFF"] * 2000, printable_area=["bad"],
                                 cool_plate_temp=["0"], cool_plate_temp_initial_layer=["0"])
        for args in ((), ("--json",)):
            t0 = time.time()
            r = self.verify(ps, *args, plate_meta=meta)
            self.assertLess(time.time() - t0, 30)
            self.assertLess(len(r.stdout), 600000, len(r.stdout))
            self.assertEqual(r.returncode, 1)
            self.assertIn("more plate x filament bed temperature rows, not checked", r.stdout)
            self.assertIn("more than 200 plates: only the first 200 were checked", r.stdout)
            self.assertClean(r)
        d = json.loads(r.stdout)
        self.assertLessEqual(sum(len(b["temps"]) for b in d["build_plates"]), 2000)
        self.assertLessEqual(len(d["build_plates"]), 200)

    def test_absurd_temperatures_are_missing(self):
        for v in ("1e308", "501", "-1", "99999999999999999", "1e3"):
            with self.subTest(v):
                r = self.verify(fx.project_settings(cool_plate_temp=[v], cool_plate_temp_initial_layer=[v]), "--json")
                d = json.loads(r.stdout)
                self.assertEqual(d["build_plates"][0]["temps"][0]["first_layer"], None)
                self.assertTrue(any("no cool_plate_temp_initial_layer or cool_plate_temp" in p for p in d["problems"]))
        d = json.loads(self.verify(fx.project_settings(cool_plate_temp=["500"], cool_plate_temp_initial_layer=["0.0"]),
                                   "--json").stdout)
        self.assertEqual(d["build_plates"][0]["temps"][0]["other_layers"], 500)
        self.assertEqual(d["build_plates"][0]["temps"][0]["first_layer"], 0)

    def test_odd_values_do_not_crash(self):
        for v in (["x"], [None], [], "50", [["1"]], ["9" * 400], [True]):
            r = self.verify(fx.project_settings(curr_bed_type=["Cool Plate"], cool_plate_temp=v,
                                                cool_plate_temp_initial_layer=v), "--json")
            self.assertNotIn("not checked", r.stdout)
            json.loads(r.stdout)
            self.assertClean(r)


if __name__ == "__main__":
    unittest.main()
