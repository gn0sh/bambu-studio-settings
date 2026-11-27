"""registry.py: the normal workflow (init, add, record, spec, validate) and the allowlist's source."""
import json
import os
import re
import unittest

import ptt_fixtures as fx
import registry


class Workflow(fx.TempDir):
    def reg(self, *args):
        return fx.run("registry.py", "--registry", self.path("filaments.json"), *args)

    def test_round_trip(self):
        rc, out, err = self.reg("init", "--printer", "P1S=Bambu Lab P1S 0.4 nozzle|0.20mm Standard @BBL X1C|Textured PEI Plate")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.reg("add", "PLA-NAVY-01", "--material", "PLA", "--color", "Navy", "--hex", "#1B2A4A")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "strings", "--feature",
                                "stringing=bad", "--date", "2026-11-27")
        self.assertEqual(rc, 0, err)
        self.assertIn("T-20261127-P1S-01", out)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "stringing gone", "--date",
                                "2026-11-27", "--set-filament", "filament_retraction_length=1.0",
                                "--set-process", "xy_hole_compensation=0.05", "--set-process", "seam_gap=10%",
                                "--status", "tuned")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), {"plate": {"filament_retraction_length": "1.0", "xy_hole_compensation": "0.05",
                                                     "seam_gap": "10%", "filament_color": "#1B2A4A",
                                                     "curr_bed_type": "Textured PEI Plate"}})
        rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S", "--for", "preset")
        self.assertEqual(json.loads(out), {"filament_retraction_length": "1.0"})
        rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S", "--for", "process-preset")
        self.assertEqual(json.loads(out), {"xy_hole_compensation": "0.05", "seam_gap": "10%"})
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("registry OK: 1 filaments", out)
        rc, out, err = self.reg("list")
        self.assertEqual(rc, 0, err)
        self.assertIn("PLA-NAVY-01", out)

    def test_dry_run_writes_nothing(self):
        p = self.write_registry(fx.registry())
        before = self.read_bytes(p)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x", "--dry-run",
                                "--set-filament", "nozzle_temperature=215")
        self.assertEqual(rc, 0, err)
        self.assertIn("dry run", out)
        self.assertEqual(self.read_bytes(p), before)
        self.assertFalse(os.path.exists(p + ".bak"))

    def test_add_checks_hex(self):
        self.reg("init", "--printer", "P1S=a|b|Cool Plate")
        rc, out, err = self.reg("add", "PLA-NAVY-01", "--material", "PLA", "--color", "Navy", "--hex", "navy")
        self.assertNotEqual(rc, 0)
        self.assertIn("--hex", err)

    def test_non_string_test_ids_refused(self):
        self.write_registry(fx.registry(filament={"nozzle_temperature": {"value": "215", "tests": [["x"], {"a": 1}],
                                                                         "why": "x"}}))
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1, out + err)
        self.assertNotIn("Traceback", err)
        self.assertIn("nozzle_temperature.tests[0]: must be a string, not a list", err)
        self.assertIn("nozzle_temperature.tests[1]: must be a string, not an object", err)

    def test_validate_cross_references(self):
        """What the schema cannot see: a value with no test, a cited test that is missing, unknown printers,
        duplicate codes. validate reports them on stdout and exits 1."""
        reg = fx.registry(filament={"nozzle_temperature": {"value": "215", "tests": []}},
                          process={"seam_gap": fx.setting("10%", "T-20261127-P1S-09")})
        f = reg["filaments"][0]
        f["presets"] = {"X1C": "Bambu PLA Basic @BBL X1C"}
        f["tests"][0]["printer"] = "A1"
        reg["filaments"].append(dict(f))
        self.write_registry(reg)
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1, out + err)
        for text in ("duplicate codes", "nozzle_temperature has no test behind it", "cites missing test T-20261127-P1S-09",
                     "preset for unknown printer X1C", "unknown printer A1"):
            self.assertIn(text, out)

    def test_test_ids_per_printer(self):
        """A printer named P1S-2 does not shift P1S's numbers, and numbers continue after the highest one."""
        reg = fx.registry()
        reg["printers"]["P1S-2"] = dict(reg["printers"]["P1S"])
        f = reg["filaments"][0]
        f["tuned"]["P1S-2"] = {"status": "untested", "filament": {}, "process": {}}
        f["tests"] = [dict(f["tests"][0], id=i, printer=i.split("-", 2)[2].rsplit("-", 1)[0])
                      for i in ("T-20261127-P1S-2-01", "T-20261127-P1S-2-02", "T-20261127-P1S-03")]
        self.write_registry(reg)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x", "--date", "2026-11-27")
        self.assertEqual(rc, 0, err)
        self.assertIn("T-20261127-P1S-04", out)
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S-2", "--verdict", "x", "--date", "2026-11-27")
        self.assertIn("T-20261127-P1S-2-03", out)


class Allowlist(unittest.TestCase):
    def doc_keys(self, label):
        text = fx.text("references", "registry.md")
        m = re.search(r"^- \*\*%s\*\* block = (.*?)(?=^- )" % label, text, re.M | re.S)
        self.assertIsNotNone(m, label)
        return set(re.findall(r"`([a-z0-9_]+)`", m.group(1)))

    def test_matches_registry_md(self):
        self.assertEqual(self.doc_keys("filament"), set(registry.FILAMENT_KEYS))
        self.assertEqual(self.doc_keys("process"), set(registry.PROCESS_KEYS))

    def test_blocks_disjoint_and_safe(self):
        self.assertFalse(registry.FILAMENT_KEYS & registry.PROCESS_KEYS)
        for k in registry.FILAMENT_KEYS | registry.PROCESS_KEYS:
            self.assertNotIn("gcode", k)
            self.assertNotIn("post_process", k)

    def test_keys_known_to_bambu_studio(self):
        p = os.path.join(fx.SKILL, "..", "bambu-studio-settings", "assets", "known_setting_keys.txt")
        if not os.path.exists(p):
            self.skipTest("bambu-studio-settings skill not next to this one")
        known = set(l.strip() for l in fx.text(p).splitlines())
        self.assertEqual((registry.FILAMENT_KEYS | registry.PROCESS_KEYS) - known, set())

    def test_reading_guide_keys_are_storable(self):
        """Every setting the reading guide tells the agent to change can be recorded (machine and project ones
        excepted), so the allowlist does not break the workflow."""
        text = fx.text("references", "reading-guide.md")
        keys = set(re.findall(r"`([a-z][a-z0-9_]+)`", text)) - {"key_name", "spec", "wipe", "curr_bed_type"}
        self.assertEqual(keys - (registry.FILAMENT_KEYS | registry.PROCESS_KEYS), set())


class BuildPlate(fx.TempDir):
    """Results belong to printer + plate + filament: init needs a plate, records carry one, spec sets it."""

    def reg(self, *args):
        return fx.run("registry.py", "--registry", self.path("filaments.json"), *args)

    def doc(self):
        with open(self.path("filaments.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_init_needs_a_plate(self):
        for arg in ("P1S=Bambu Lab P1S 0.4 nozzle|0.20mm Standard @BBL X1C",
                    "P1S=Bambu Lab P1S 0.4 nozzle|0.20mm Standard @BBL X1C|"):
            with self.subTest(arg):
                rc, out, err = self.reg("init", "--printer", arg)
                self.assertNotEqual(rc, 0)
                self.assertIn("no build plate for printer P1S: name the build plate on the printer", err)
                self.assertIn("Textured PEI Plate", err)
                self.assertNotIn("Traceback", err)
                self.assertFalse(os.path.exists(self.path("filaments.json")))

    def test_init_refuses_unknown_plate(self):
        for plate in ("Textured PEI", "textured pei plate", "PEI", "Glass Plate"):
            with self.subTest(plate):
                rc, out, err = self.reg("init", "--printer", "P1S=Bambu Lab P1S 0.4 nozzle|0.20mm Standard @BBL X1C|"
                                        + plate)
                self.assertNotEqual(rc, 0)
                self.assertIn("printers.P1S.plate", err)
                self.assertIn("is not one of Cool Plate, Engineering Plate", err)
                self.assertFalse(os.path.exists(self.path("filaments.json")))

    def start(self, plate="Cool Plate"):
        rc, out, err = self.reg("init", "--printer", "P1S=Bambu Lab P1S 0.4 nozzle|0.20mm Standard @BBL X1C|" + plate)
        self.assertEqual(rc, 0, err)
        rc, out, err = self.reg("add", "PLA-NAVY-01", "--material", "PLA", "--color", "Navy", "--hex", "#1B2A4A")
        self.assertEqual(rc, 0, err)

    def test_every_plate_round_trips_to_spec(self):
        for plate in registry.PLATE_NAMES:
            with self.subTest(plate):
                os.path.exists(self.path("filaments.json")) and os.unlink(self.path("filaments.json"))
                self.start(plate)
                rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S")
                self.assertEqual(rc, 0, err)
                self.assertEqual(json.loads(out)["plate"]["curr_bed_type"], plate)
                self.assertIn("# build plate: %s" % plate, err)
                rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S", "--for", "preset")
                self.assertNotIn("curr_bed_type", json.loads(out))
                self.assertIn("set curr_bed_type to it in the project", err)

    def test_record_carries_the_printer_plate(self):
        self.start("Engineering Plate")
        rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "ok", "--date", "2026-11-27")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.doc()["filaments"][0]["tests"][0]["plate"], "Engineering Plate")

    def test_record_on_another_plate(self):
        self.start("Cool Plate")
        with open(self.path("rec.json"), "w") as fh:
            json.dump({"code": "PLA-NAVY-01", "printer": "P1S", "plate": "Textured PEI Plate", "verdict": "ok",
                       "date": "2026-11-27"}, fh)
        rc, out, err = self.reg("record", "--from", self.path("rec.json"))
        self.assertEqual(rc, 0, err)
        d = self.doc()["filaments"][0]
        self.assertEqual(d["tests"][0]["plate"], "Textured PEI Plate")
        self.assertEqual(d["tuned"]["P1S"]["status"], "untested")      # another plate's tile is not a baseline here
        before = self.read_bytes(self.path("filaments.json"))
        for extra in (["--set-filament", "nozzle_temperature=215"], ["--set-process", "seam_gap=10%"],
                      ["--status", "tuned"], ["--why", "x"]):
            with self.subTest(extra):
                rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--plate", "Textured PEI Plate",
                                        "--verdict", "x", *extra)
                self.assertNotEqual(rc, 0)
                self.assertIn("results belong to printer + plate + filament", err)
                self.assertEqual(self.read_bytes(self.path("filaments.json")), before)

    def test_record_refuses_unknown_plate(self):
        self.start()
        before = self.read_bytes(self.path("filaments.json"))
        for plate in ("Textured PEI", "", "-Cool Plate", "Cool Plate\n", "Cool Plate$(id)", "x" * 200):
            with self.subTest(plate):
                rc, out, err = self.reg("record", "PLA-NAVY-01", "--printer", "P1S", "--plate", plate, "--verdict", "x")
                self.assertNotEqual(rc, 0)
                self.assertIn("filaments[0].tests[0].plate", err)
                self.assertNotIn("Traceback", err)
                self.assertEqual(self.read_bytes(self.path("filaments.json")), before)

    def test_from_plate_must_be_text(self):
        self.start()
        for v in (1, True, None, ["Cool Plate"], {"a": "b"}):
            with self.subTest(v):
                with open(self.path("rec.json"), "w") as fh:
                    json.dump({"code": "PLA-NAVY-01", "printer": "P1S", "plate": v, "verdict": "ok"}, fh)
                rc, out, err = self.reg("record", "--from", self.path("rec.json"))
                self.assertNotEqual(rc, 0)
                self.assertIn("plate must be a string", err)

    def test_old_records_without_plate_are_valid(self):
        p = self.write_registry(fx.registry(filament={"nozzle_temperature": fx.setting("215")}))
        self.assertNotIn("plate", fx.registry()["filaments"][0]["tests"][0])
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(os.path.exists(p))

    def test_validate_flags_values_from_another_plate(self):
        reg = fx.registry(filament={"nozzle_temperature": fx.setting("215")})
        reg["filaments"][0]["tests"][0]["plate"] = "Cool Plate"           # the printer's plate is Textured PEI
        self.write_registry(reg)
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 1)
        self.assertIn("printed on Cool Plate, but P1S's plate is Textured PEI Plate", out)


class LegacyPlate(fx.TempDir):
    """registry-trust/legacy-plate: a registry from before v1.1 whose printer plate is another preset-safe name is
    refused by every command with the set-plate command that fixes it; set-plate fixes only that."""

    HINT = 'registry.py set-plate P1S "<plate on the printer>"'

    def reg(self, *args):
        return fx.run("registry.py", "--registry", self.path("filaments.json"), *args)

    def legacy(self, plate="Textured PEI", **printers):
        reg = fx.registry(filament={"nozzle_temperature": fx.setting("215")})
        reg["printers"]["P1S"]["plate"] = plate
        for name, pl in printers.items():
            reg["printers"][name] = dict(reg["printers"]["P1S"], plate=pl)
        return self.write_registry(reg)

    def test_every_command_names_set_plate(self):
        p = self.legacy()
        before = self.read_bytes(p)
        with open(self.path("rec.json"), "w") as fh:
            json.dump({"code": "PLA-NAVY-01", "printer": "P1S", "verdict": "x"}, fh)
        for args in (["list"], ["show", "PLA-NAVY-01"], ["spec", "PLA-NAVY-01", "--printer", "P1S"], ["validate"],
                     ["record", "--from", self.path("rec.json")],
                     ["add", "PLA-RED-01", "--material", "PLA", "--color", "Red", "--hex", "#FF0000"]):
            with self.subTest(args[0]):
                rc, out, err = self.reg(*args)
                self.assertNotEqual(rc, 0)
                self.assertIn("printers.P1S.plate: 'Textured PEI' is not one of", err)
                self.assertIn(self.HINT, err)
                self.assertNotIn("Traceback", err)
                self.assertEqual(self.read_bytes(p), before)

    def test_set_plate_fixes_it(self):
        p = self.legacy()
        before = self.read_bytes(p)
        rc, out, err = self.reg("set-plate", "P1S", "Cool Plate")
        self.assertEqual(rc, 0, err)
        self.assertIn("printer P1S: plate Textured PEI -> Cool Plate", out)
        self.assertEqual(self.read_bytes(p + ".bak"), before)
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["printers"]["P1S"]["plate"], "Cool Plate")
        rc, out, err = self.reg("validate")
        self.assertEqual(rc, 0, out + err)

    def test_set_plate_refuses_bad_input(self):
        p = self.legacy()
        before = self.read_bytes(p)
        for args in ([], ["P1S"], ["P1S", "Textured PEI"], ["P1S", "cool plate"], ["P1S", "Cool Plate;rm"],
                     ["P1S;x", "Cool Plate"], ["X1C", "Cool Plate"], ["P1S", "Cool Plate", "X1C"]):
            with self.subTest(args):
                rc, out, err = self.reg("set-plate", *args)
                self.assertNotEqual(rc, 0)
                self.assertNotIn("Traceback", err)
                self.assertEqual(self.read_bytes(p), before)
                self.assertFalse(os.path.exists(p + ".bak"))

    def test_set_plate_does_not_loosen_anything_else(self):
        reg = fx.registry()
        reg["printers"]["P1S"]["plate"] = "Textured PEI"
        reg["filaments"][0]["color"]["hex"] = "navy"
        p = self.write_registry(reg)
        before = self.read_bytes(p)
        rc, out, err = self.reg("set-plate", "P1S", "Cool Plate")
        self.assertNotEqual(rc, 0)
        self.assertIn("filaments[0].color.hex", err)
        self.assertEqual(self.read_bytes(p), before)
        for plate in ("x;rm -rf ~", "-Cool", 5, ["Cool Plate"]):          # never valid, not an old name either
            with self.subTest(plate):
                p = self.legacy(plate)
                before = self.read_bytes(p)
                rc, out, err = self.reg("list")
                self.assertNotIn("set-plate", err)
                rc, out, err = self.reg("set-plate", "P1S", "Cool Plate")
                self.assertNotEqual(rc, 0)
                self.assertEqual(self.read_bytes(p), before)

    def test_two_old_plates(self):
        p = self.legacy(X1C="PEI")
        before = self.read_bytes(p)
        rc, out, err = self.reg("list")
        self.assertIn('registry.py set-plate P1S "<plate on the printer>" X1C "<plate on the printer>"', err)
        rc, out, err = self.reg("set-plate", "P1S", "Cool Plate")
        self.assertNotEqual(rc, 0)
        self.assertIn("printer X1C also has an old plate name", err)
        self.assertEqual(self.read_bytes(p), before)
        rc, out, err = self.reg("set-plate", "P1S", "Cool Plate", "X1C", "Textured PEI Plate")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.reg("list")
        self.assertEqual(rc, 0, err)

    def test_set_plate_on_a_valid_registry(self):
        self.write_registry(fx.registry())
        rc, out, err = self.reg("set-plate", "P1S", "Engineering Plate")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.reg("spec", "PLA-NAVY-01", "--printer", "P1S")
        self.assertEqual(json.loads(out)["plate"]["curr_bed_type"], "Engineering Plate")


if __name__ == "__main__":
    unittest.main()
