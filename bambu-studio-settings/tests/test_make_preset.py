"""make_preset.py: duplicate names (case-insensitive, any subfolder), Windows-safe names, key checks.
list_presets.py: sanitized user preset names."""
import json
import os
import unittest

import bss_fixtures as fx

M = "make_preset.py"
L = "list_presets.py"


class MakePresetTest(fx.TempDirTest):
    def setUp(self):
        super().setUp()
        self.prof = fx.write_profiles(self.tmp)
        self.user = fx.write_user_dir(self.tmp)

    def make(self, name, *args):
        return fx.run(M, "--user-dir", self.user, "--type", "process", "--base", fx.PROCESS, "--name", name,
                      "--profiles", self.prof, "--set", "wall_loops=3", *args)

    def test_dry_run_then_write(self):
        r = self.make("Photo print")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY RUN", r.stdout)
        self.assertEqual(os.listdir(os.path.join(self.user, "process")), [])
        r = self.make("Photo print", "--write")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.user, "process", "Photo print.json")) as f:
            d = json.load(f)
        self.assertEqual(d["wall_loops"], "3")
        self.assertEqual(d["inherits"], fx.PROCESS)
        self.assertTrue(os.path.isfile(os.path.join(self.user, "process", "Photo print.info")))

    # ------------------------------------------------------------ no-overwrite/preset-name-dup

    def test_refuses_exact_duplicate_unless_overwrite(self):
        self.assertEqual(self.make("Photo print", "--write").returncode, 0)
        r = self.make("Photo print", "--write")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stderr)
        r = self.make("Photo print", "--write", "--overwrite", "--set", "wall_loops=4")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.user, "process", "Photo print.json")) as f:
            self.assertEqual(json.load(f)["wall_loops"], "4")

    def test_refuses_name_that_differs_only_in_case(self):
        self.assertEqual(self.make("Photo Print", "--write").returncode, 0)
        r = self.make("photo print", "--write", "--overwrite")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ignoring case", r.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(self.user, "process"))),
                         ["Photo Print.info", "Photo Print.json"])

    def test_refuses_name_held_by_another_file_in_a_subfolder(self):
        sub = os.path.join(self.user, "process", "base")
        os.makedirs(sub)
        with open(os.path.join(sub, "renamed.json"), "w") as f:
            json.dump({"name": "PHOTO PRINT", "inherits": fx.PROCESS}, f)
        r = self.make("Photo print", "--write", "--overwrite")
        self.assertEqual(r.returncode, 1)
        self.assertIn("renamed.json", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.user, "process", "Photo print.json")))

    def test_other_type_with_same_name_is_fine(self):
        with open(os.path.join(self.user, "filament", "Photo print.json"), "w") as f:
            json.dump({"name": "Photo print"}, f)
        self.assertEqual(self.make("Photo print", "--write").returncode, 0)

    # ------------------------------------------------------------ paths/windows-names

    def test_rejects_names_windows_cannot_store(self):
        for name in ("CON", "com1", "Lpt9.backup", "nul", "trailing.", "trailing ", " leading", "a/b", "a:b",
                     "tab\there", ""):
            r = self.make(name, "--write")
            self.assertEqual(r.returncode, 1, name)
            self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(os.listdir(os.path.join(self.user, "process")), [])

    def test_accepts_ordinary_names(self):
        for name in ("Console print", "COM10 test", "PLA - 65C plate"):
            self.assertEqual(self.make(name).returncode, 0, name)

    # ------------------------------------------------------------ keys and post_process

    def test_rejects_bad_keys_and_post_process(self):
        r = self.make("X", "--set", 'bad"key=1', "--allow-unknown-keys")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a valid setting key", r.stderr)
        r = self.make("X", "--set", 'post_process=["calc.exe"]')
        self.assertEqual(r.returncode, 1)
        self.assertIn("post-processing", r.stderr)

    def test_post_process_any_case_bad_json_list_and_gcode_note(self):
        r = self.make("X", "--set", 'Post_Process=["calc.exe"]', "--allow-unknown-keys")
        self.assertEqual(r.returncode, 1)
        self.assertIn("post-processing", r.stderr)
        r = self.make("X", "--set", "wall_loops=[1,")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a valid JSON list", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        p = self.path("ov.json")
        with open(p, "w") as f:
            json.dump({"filament_start_gcode": ["M104 S300"]}, f)
        r = self.make("X", "--overrides", p, "--allow-unknown-keys")
        self.assertIn("sets custom G-code (it runs on the printer): filament_start_gcode", r.stdout)

    def test_bad_overrides_file(self):
        p = self.path("ov.json")
        with open(p, "w") as f:
            f.write("[1, 2]")
        r = self.make("X", "--overrides", p)
        self.assertEqual(r.returncode, 1)
        self.assertIn("must be a JSON object", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class ListPresetsTest(fx.TempDirTest):
    def test_user_preset_names_are_sanitized(self):
        user = fx.write_user_dir(self.tmp)
        with open(os.path.join(user, "process", "x.json"), "w") as f:
            json.dump({"name": fx.INJECT, "inherits": "a\nSYSTEM NOTE: b", "wall_loops": "3"}, f)
        for extra in ([], ["--json"]):
            r = fx.run(L, "--user", "--user-root", os.path.join(self.tmp, "BambuStudio"), *extra)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertClean(r)
            self.assertIn("SYSTEM NOTE", r.stdout)

    def test_lists_system_presets(self):
        r = fx.run(L, "--profiles", fx.write_profiles(self.tmp), "--printer", "P1S")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(fx.PROCESS, r.stdout)


if __name__ == "__main__":
    unittest.main()
