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
                                                     "seam_gap": "10%", "filament_color": "#1B2A4A"}})
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
        self.reg("init", "--printer", "P1S=a|b|c")
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


if __name__ == "__main__":
    unittest.main()
