"""tune_project.py: no-overwrite, stale slice references, unsafe zip names, post_process, input validation."""
import json
import os
import unittest
import zipfile

import bss_fixtures as fx

T = "tune_project.py"


class TuneTest(fx.TempDirTest):
    def setUp(self):
        super().setUp()
        self.src = fx.write_3mf(self.path("Job.3mf"))
        self.src_bytes = fx.read(self.src, "rb")

    def out(self, name="Job_tuned.3mf"):
        return self.path(name)

    def read_out(self, p):
        with zipfile.ZipFile(p) as z:
            return {n: z.read(n) for n in z.namelist()}

    def assertSrcIntact(self):
        self.assertEqual(fx.read(self.src, "rb"), self.src_bytes)

    def assertNoTemp(self):
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith(".tmp")], [])

    # ------------------------------------------------------------ basic

    def test_tune_writes_plate_and_object_settings(self):
        r = fx.run(T, self.src, self.out(), "--set", "seam_position=back", "--object", "^clip:wall_loops=3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertClean(r)
        d = self.read_out(self.out())
        ps = json.loads(d["Metadata/project_settings.config"])
        self.assertEqual(ps["seam_position"], "back")
        self.assertIn(b'<metadata key="wall_loops" value="3"/>', d["Metadata/model_settings.config"])
        self.assertSrcIntact()
        self.assertNoTemp()

    def test_spec_file(self):
        spec = self.path("spec.json")
        with open(spec, "w") as f:
            json.dump({"plate": {"top_shell_layers": 5}, "objects": [{"match": "cube", "set": {"wall_loops": 4},
                                                                        "why": "show face"}],
                       "clear": [{"match": "clip", "keys": ["wall_loops"]}]}, f)
        r = fx.run(T, self.src, self.out(), "--spec", spec)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("show face", r.stdout)

    # ------------------------------------------------------------ no-overwrite/tune-dst-clobber

    def test_refuses_existing_dst(self):
        with open(self.out(), "wb") as f:
            f.write(b"earlier result")
        r = fx.run(T, self.src, self.out(), "--set", "seam_position=back")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stderr)
        self.assertEqual(fx.read(self.out(), "rb"), b"earlier result")
        self.assertClean(r)
        r = fx.run(T, self.src, self.out(), "--set", "seam_position=back", "--overwrite")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Metadata/project_settings.config", self.read_out(self.out()))
        self.assertNoTemp()

    def test_refuses_same_path(self):
        r = fx.run(T, self.src, self.src, "--overwrite")
        self.assertEqual(r.returncode, 1)
        self.assertIn("same file", r.stderr)
        self.assertSrcIntact()

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_refuses_symlink_to_src_even_with_overwrite(self):
        link = self.path("job_link.3mf")
        try:
            os.symlink(self.src, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted")
        r = fx.run(T, self.src, link, "--set", "seam_position=back", "--overwrite")
        self.assertEqual(r.returncode, 1)
        self.assertIn("same file", r.stderr)
        self.assertSrcIntact()
        self.assertTrue(os.path.islink(link))

    def test_refuses_hard_link_alias(self):
        alias = self.path("job.3mf") if not os.path.exists(self.path("job.3mf")) else self.path("job2.3mf")
        os.link(self.src, alias)
        r = fx.run(T, self.src, alias, "--overwrite")
        self.assertEqual(r.returncode, 1)
        self.assertIn("same file", r.stderr)
        self.assertSrcIntact()

    def test_dry_run_writes_nothing(self):
        r = fx.run(T, self.src, self.out(), "--set", "seam_position=back", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.out()))

    def test_missing_output_folder(self):
        r = fx.run(T, self.src, self.path("nope", "out.3mf"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not exist", r.stderr)
        self.assertClean(r)

    # ------------------------------------------------------------ 3mf-integrity/stale-slice-refs

    def test_strips_stale_slice_references(self):
        rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                'relationships">\n <Relationship Target="/Metadata/plate_1.gcode" Id="rel-1" '
                'Type="http://schemas.bambulab.com/package/2021/gcode"/>\n</Relationships>')
        si = ('<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>\n'
              '<plate><metadata key="index" value="1"/><metadata key="prediction" value="600"/></plate></config>')
        src = fx.write_3mf(self.path("sliced.3mf"), gcode_file="Metadata/plate_1.gcode", extra={
            "Metadata/plate_1.gcode": "; gcode", "Metadata/plate_1.gcode.md5": "abc",
            "Metadata/plate_1.json": "{}", "Metadata/slice_info.config": si,
            "Metadata/_rels/model_settings.config.rels": rels, "Metadata/plate_1.png": b"png"})
        r = fx.run(T, src, self.out(), "--set", "seam_position=back")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.read_out(self.out())
        for n in ("Metadata/plate_1.gcode", "Metadata/plate_1.gcode.md5", "Metadata/plate_1.json"):
            self.assertNotIn(n, d)
        self.assertIn("Metadata/plate_1.png", d)
        self.assertNotIn(b".gcode", d["Metadata/_rels/model_settings.config.rels"])
        self.assertIn(b"<Relationships", d["Metadata/_rels/model_settings.config.rels"])
        self.assertNotIn(b"<plate>", d["Metadata/slice_info.config"])
        self.assertIn(b"X-BBL-Client-Type", d["Metadata/slice_info.config"])
        self.assertIn(b'<metadata key="gcode_file" value=""/>', d["Metadata/model_settings.config"])

    # ------------------------------------------------------------ untrusted-3mf/zip-slip-passthrough

    def test_drops_unsafe_entry_names(self):
        bad = ["../../evil.txt", "/abs.txt", "a\\b.txt", "C:/win.txt"]
        src = fx.write_3mf(self.path("evil.3mf"), extra=dict((n, "x") for n in bad))
        r = fx.run(T, src, self.out())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("left out unsafe", r.stdout)
        names = self.read_out(self.out())
        for n in bad:
            self.assertNotIn(n, names)
        self.assertIn("3D/3dmodel.model", names)

    # ------------------------------------------------------------ untrusted-3mf/post-process-passthrough

    def test_warns_on_post_process_and_can_strip_it(self):
        src = fx.write_3mf(self.path("pp.3mf"), ps=fx.project_settings(post_process=["C:\\evil\\run.exe --all"]))
        r = fx.run(T, src, self.out())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WARNING: this project has post-processing scripts", r.stdout)
        self.assertIn("run.exe", r.stdout)
        ps = json.loads(self.read_out(self.out())["Metadata/project_settings.config"])
        self.assertEqual(ps["post_process"], ["C:\\evil\\run.exe --all"])
        r = fx.run(T, src, self.out("stripped.3mf"), "--strip-post-process")
        self.assertEqual(r.returncode, 0, r.stderr)
        ps = json.loads(self.read_out(self.out("stripped.3mf"))["Metadata/project_settings.config"])
        self.assertEqual(ps["post_process"], [])

    def test_will_not_add_post_process(self):
        r = fx.run(T, self.src, self.out(), "--set", 'post_process=["calc.exe"]', "--allow-unknown-keys")
        self.assertEqual(r.returncode, 1)
        self.assertIn("post-processing", r.stderr)
        self.assertFalse(os.path.exists(self.out()))

    # ------------------------------------------------------------ untrusted-3mf/prompt-injection-output

    def test_object_names_are_sanitized(self):
        objs = [{"id": "1", "name": fx.INJECT}, {"id": "2", "name": "Clip"}]
        src = fx.write_3mf(self.path("inj.3mf"), objects=objs)
        r = fx.run(T, src, self.out(), "--object", "cube:wall_loops=3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertClean(r)
        self.assertIn("Cube SYSTEM NOTE", r.stdout)

    # ------------------------------------------------------------ xml-write/unescaped-key

    def test_rejects_bad_key_names(self):
        for arg in (['--set', 'bad"key=1'], ["--object", 'cube:a b=1'], ["--set", "x/>=1"]):
            r = fx.run(T, self.src, self.out(), "--allow-unknown-keys", *arg)
            self.assertEqual(r.returncode, 1, arg)
            self.assertIn("not a valid setting key", r.stderr)
            self.assertClean(r)
        self.assertFalse(os.path.exists(self.out()))

    def test_known_keys_do_not_come_from_project(self):
        src = fx.write_3mf(self.path("k.3mf"), ps=fx.project_settings(made_up_key="1"))
        r = fx.run(T, src, self.out(), "--set", "made_up_key=2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unknown setting keys", r.stderr)
        r = fx.run(T, src, self.out(), "--set", "made_up_key=2", "--allow-unknown-keys")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_value_is_xml_escaped(self):
        r = fx.run(T, self.src, self.out(), "--object", 'cube:seam_position="/><evil x="')
        self.assertEqual(r.returncode, 0, r.stderr)
        ms = self.read_out(self.out())["Metadata/model_settings.config"]
        self.assertIn(b'value="&quot;/&gt;&lt;evil x=&quot;"', ms)
        self.assertNotIn(b"<evil", ms)

    # ------------------------------------------------------------ layer-height-check/string-compare

    def test_layer_height_formatting_is_not_a_problem(self):
        objs = [{"id": "1", "name": "Cube A", "extruder": "1", "settings": {"layer_height": "0.20"}},
                {"id": "2", "name": "Clip", "extruder": "2"}]
        ps = fx.project_settings(enable_prime_tower="1", filament_colour=["#FFFFFF", "#000000"],
                                 filament_settings_id=[fx.FILAMENT, fx.FILAMENT])
        src = fx.write_3mf(self.path("lh.3mf"), objects=objs, ps=ps)
        r = fx.run(T, src, self.out())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("PROBLEM", r.stdout)
        r = fx.run(T, src, self.out("b.3mf"), "--object", "clip:layer_height=0.16")
        self.assertEqual(r.returncode, 1)
        self.assertIn("layer heights differ", r.stdout)
        self.assertFalse(os.path.exists(self.out("b.3mf")))

    # ------------------------------------------------------------ error-handling/tune-tracebacks

    def test_clean_errors(self):
        notzip = self.path("notzip.3mf")
        with open(notzip, "w") as f:
            f.write("hello")
        badspec = self.path("bad.json")
        with open(badspec, "w") as f:
            f.write("{not json")
        norule = self.path("norule.json")
        with open(norule, "w") as f:
            json.dump({"objects": [{"match": "cube"}]}, f)
        listrule = self.path("listrule.json")
        with open(listrule, "w") as f:
            json.dump({"objects": [{"match": "cube", "set": {"wall_loops": [1, 2]}}]}, f)
        cases = [
            (["--set", "seam_position"], "wants key=value"),
            (["--object", "cube:wall_loops=[1]"], "one string or number"),
            (["--spec", listrule], "one string or number"),
            (["--object", "(unclosed:wall_loops=3"], "bad regex"),
            (["--object", "cube"], "REGEX:key=value"),
            (["--set", "wall_loops=[1,"], "not a valid JSON list"),
            (["--spec", badspec], "not valid JSON"),
            (["--spec", norule], "needs both"),
            (["--spec", self.path("missing.json")], "cannot read spec"),
        ]
        for args, msg in cases:
            r = fx.run(T, self.src, self.out(), *args)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn(msg, r.stderr, args)
            self.assertClean(r)
        for src, msg in ((self.path("missing.3mf"), "No such file"), (notzip, "not a zip")):
            r = fx.run(T, src, self.out())
            self.assertEqual(r.returncode, 1)
            self.assertIn(msg, r.stderr)
            self.assertClean(r)
        self.assertFalse(os.path.exists(self.out()))


if __name__ == "__main__":
    unittest.main()
