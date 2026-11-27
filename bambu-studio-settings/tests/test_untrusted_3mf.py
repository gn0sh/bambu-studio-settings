"""ADR 0006 regression tests: the one reader (open_project) and the one detector (find_post_process).

Problem family untrusted-3mf/post-process-passthrough: every way a downloaded .3mf could get a post_process past
the scripts that Bambu Studio would still load. Each fixture is checked against verify, tune and slice_check."""
import json
import os
import sys
import time
import tracemalloc
import unittest
import zipfile
from unittest import mock

import bss_fixtures as fx
import bambu_common as bc
import slice_check

PS = "Metadata/project_settings.config"
MS = "Metadata/model_settings.config"
EVIL = "C:\\evil\\run.exe"


def ms_with(extra_object_meta="", name="Cube A"):
    """model_settings.config text with one object; extra_object_meta goes in as raw XML on the object."""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="1">\n'
            '    <metadata key="name" value="%s"/>\n    <metadata key="extruder" value="1"/>\n%s'
            '    <part id="1" subtype="normal_part">\n      <metadata key="name" value="Cube A"/>\n    </part>\n'
            '  </object>\n  <plate>\n    <metadata key="plater_id" value="1"/>\n'
            '    <model_instance>\n      <metadata key="object_id" value="1"/>\n    </model_instance>\n'
            '  </plate>\n</config>\n' % (name, extra_object_meta))


ONE_OBJECT = [{"id": "1", "name": "Cube A"}]


class Untrusted3mfBase(fx.TempDirTest):
    """Helpers shared by the ADR 0006 tests (no tests of its own)."""

    def setUp(self):
        super().setUp()
        self.prof = fx.write_profiles(self.tmp)

    # ------------------------------------------------------------ helpers

    def raw(self, name, entries):
        return fx.write_raw_zip(self.path(name), entries)

    def project(self, name, ps=None, ms=None, extra=(), drop=(), model=None):
        """A project with the standard entries (minus drop) plus extra [(name, data, utf8)] at the end."""
        base = [e for e in fx.project_entries(ps=ps, ms=ms, model=model, objects=ONE_OBJECT) if e[0] not in drop]
        return self.raw(name, base + list(extra))

    def verify(self, path, *args):
        return fx.run("verify_project.py", path, "--profiles", self.prof, *args)

    def tune(self, src, out, *args):
        return fx.run("tune_project.py", src, out, "--set", "seam_position=back", *args)

    def slice_refused(self, src):
        """slice_check stops before it runs anything (the exe is never started for a refused file)."""
        return fx.run("slice_check.py", src, "--exe", sys.executable)

    def entries(self, path):
        with zipfile.ZipFile(path) as z:
            return dict((i.filename, z.read(i)) for i in z.infolist())

    def assertRefused(self, src, why):
        r = self.verify(src)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("PROBLEM: archive refused: ", r.stdout)
        self.assertIn(why, r.stdout)
        self.assertClean(r)
        d = json.loads(self.verify(src, "--json").stdout)
        self.assertIn(why, d["refused"])
        out = self.path("out_%s" % os.path.basename(src))
        r = self.tune(src, out, "--strip-post-process")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("archive refused", r.stderr)
        self.assertFalse(os.path.exists(out))
        r = self.slice_refused(src)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("archive refused", r.stderr)
        self.assertClean(r)
        self.assertEqual([f for f in os.listdir(self.tmp) if f.endswith(".tmp")], [])

    def findings(self, src):
        with bc.open_project(src) as idx:
            return idx.findings

    def assertStripsClean(self, src, expect_entries):
        """tune --strip-post-process and slice_check's copy both remove every finding, and say where they were."""
        found = self.findings(src)
        self.assertEqual(sorted(set(f.entry for f in found)), sorted(expect_entries))
        out = self.path("stripped_%s" % os.path.basename(src))
        r = self.tune(src, out, "--strip-post-process")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING: this project has post-processing scripts", r.stdout)
        for e in expect_entries:
            self.assertIn(e, r.stdout)
        self.assertEqual(self.findings(out), [])
        copy = self.path("copy_%s" % os.path.basename(src))
        before, _, _ = slice_check.safe_project_copy(src, copy)
        self.assertEqual(len(before), len(found))
        self.assertEqual(self.findings(copy), [])
        r = self.verify(src)
        self.assertEqual(r.returncode, 1)
        return out, copy

    def tune_ok(self, src):
        out = self.path("ok_%s" % os.path.basename(src))
        r = self.tune(src, out)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out


class Untrusted3mfTest(Untrusted3mfBase):
    # ------------------------------------------------------------ names: case, duplicates, backslashes (NEW-H1)

    def test_case_variant_settings_name_refused(self):
        src = self.project("case.3mf", drop=(PS,), extra=[
            ("metadata/Project_Settings.config", json.dumps(fx.project_settings(post_process=[EVIL])), False)])
        self.assertRefused(src, "not spelled the way Bambu Studio writes it")

    def test_case_variant_next_to_canonical_refused(self):
        src = self.project("case2.3mf", extra=[
            ("metadata/Project_Settings.config", json.dumps(fx.project_settings(post_process=[EVIL])), False)])
        self.assertRefused(src, "same name when case and slashes are ignored")

    def test_duplicate_first_copy_has_post_process_refused(self):
        clean = dict(fx.project_settings())
        del clean["post_process"]
        src = self.project("dup.3mf", ps=fx.project_settings(post_process=[EVIL]),
                           extra=[(PS, json.dumps(clean), False)])
        self.assertRefused(src, "same name when case and slashes are ignored")

    def test_identical_clean_duplicates_refused(self):
        # the strict choice: Bambu Studio never writes two entries with one name, so neither do we read them
        src = self.project("dup2.3mf", extra=[(PS, json.dumps(fx.project_settings()), False)])
        self.assertRefused(src, "same name")

    def test_backslash_settings_name_refused(self):
        # on Windows zipfile shows this name with "/" - the reader looks at the raw central-directory name
        src = self.project("bs.3mf", drop=(PS,), extra=[
            ("Metadata\\project_settings.config", json.dumps(fx.project_settings(post_process=[EVIL])), False)])
        self.assertRefused(src, "has a backslash in its name")

    def test_backslash_name_shown_as_raw_name(self):
        src = self.project("bs2.3mf", drop=(PS,), extra=[("Metadata\\project_settings.config", "{}", False)])
        with zipfile.ZipFile(src) as z:
            info = z.infolist()[-1]
        self.assertEqual(info.orig_filename, "Metadata\\project_settings.config")

    # ------------------------------------------------------------ embedded presets (NEW-M1)

    def test_embedded_process_preset_any_extension(self):
        preset = json.dumps({"name": "p", "post_process": ["/tmp/x.sh"], "wall_loops": "2"})
        src = self.project("pre.3mf", extra=[("Metadata/process_settings_1.config", preset, False),
                                             ("Metadata/process_settings_1.txt", preset, False),
                                             ("Metadata/process_settings_2", preset, False)])
        names = ["Metadata/process_settings_1.config", "Metadata/process_settings_1.txt",
                 "Metadata/process_settings_2"]
        out, copy = self.assertStripsClean(src, names)
        for n in names:
            self.assertEqual(json.loads(self.entries(out)[n])["post_process"], [])
            self.assertEqual(json.loads(self.entries(copy)[n])["wall_loops"], "2")
        self.assertIn("post-processing script (post_process) in Metadata/process_settings_1.txt",
                      self.verify(src).stdout)

    def test_embedded_ini_preset(self):
        ini = "; comment\nlayer_height = 0.2\npost_process = x.exe\n[extra]\nwall_loops = 3\n"
        src = self.project("ini.3mf", extra=[("Metadata/print_setting_1.config", ini, False)])
        out, _ = self.assertStripsClean(src, ["Metadata/print_setting_1.config"])
        stripped = self.entries(out)["Metadata/print_setting_1.config"].decode()
        self.assertNotIn("post_process", stripped)
        self.assertIn("layer_height = 0.2", stripped)
        self.assertIn("x.exe", self.verify(src).stdout)

    def test_ini_preset_line_without_equals_refused(self):
        src = self.project("ini2.3mf", extra=[("Metadata/print_setting_1.config", "a = 1\nnot a setting\n", False)])
        self.assertRefused(src, "has no '='")

    def test_ini_preset_repeated_key_refused(self):
        src = self.project("ini3.3mf", extra=[("Metadata/print_setting_1.config", "post_process =\npost_process = x\n",
                                               False)])
        self.assertRefused(src, "repeats key")

    def test_ini_comment_mentioning_post_process_is_a_finding(self):
        src = self.project("ini4.3mf", extra=[("Metadata/print_setting_1.config", "; post_process = x.exe\n", False)])
        self.assertEqual(len(self.findings(src)), 1)
        self.assertStripsClean(src, ["Metadata/print_setting_1.config"])

    def test_filament_and_machine_presets(self):
        pp = json.dumps({"post_process": "calc.exe"})
        src = self.project("fm.3mf", extra=[("Metadata/filament_settings_1.config", pp, False),
                                            ("Metadata/machine_settings_1.config", pp, False)])
        self.assertStripsClean(src, ["Metadata/filament_settings_1.config", "Metadata/machine_settings_1.config"])

    def test_embedded_preset_gcode_is_a_note(self):
        src = self.project("g.3mf", extra=[("Metadata/machine_settings_1.config",
                                            json.dumps({"machine_start_gcode": "M104 S300"}), False)])
        r = self.verify(src)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("G-code in the embedded machine settings preset Metadata/machine_settings_1.config", r.stdout)

    def test_unparseable_preset_refused(self):
        src = self.project("bad.3mf", extra=[("Metadata/process_settings_1.config", '{"a": 1} trailing', False)])
        self.assertRefused(src, "is not valid JSON")

    # ------------------------------------------------------------ JSON forms

    def test_json_key_forms(self):
        cases = {
            "plain": '{"post_process": ["%s"]}',
            "case": '{"Post_Process": ["%s"]}',
            "escaped": '{"post\\u005fprocess": ["%s"]}',
            "nested": '{"a": {"b": [{"post_process": "%s"}]}}',
            "duplicate": '{"post_process": ["%s"], "post_process": []}',
        }
        for label, text in cases.items():
            src = self.project("j_%s.3mf" % label, ps=(text % "x.exe").encode())
            found = self.findings(src)
            self.assertEqual(len(found), 1, label)
            self.assertEqual(found[0].value, "x.exe", label)
            self.assertStripsClean(src, [PS])

    def test_empty_post_process_is_not_a_finding(self):
        for v in ([], [""], "", [" ", ""], None):
            src = self.project("e.3mf", ps=fx.project_settings(post_process=v))
            self.assertEqual(self.findings(src), [], v)

    def test_json_not_utf8_or_with_nul_refused(self):
        self.assertRefused(self.project("u16.3mf", ps='{"post_process": ["x"]}'.encode("utf-16")), "NUL bytes")
        self.assertRefused(self.project("l1.3mf", ps=b'{"name": "caf\xe9"}'), "not UTF-8")

    # ------------------------------------------------------------ model settings XML (NEW-L3)

    def test_single_quoted_reordered_attribute(self):
        src = self.project("sq.3mf", ms=ms_with("    <metadata value='%s' key='post_process'/>\n" % EVIL))
        out, copy = self.assertStripsClean(src, [MS])
        r = self.verify(src)
        self.assertIn("object-level post_process", r.stdout)
        self.assertIn("run.exe", r.stdout)
        ms = self.entries(copy)[MS].decode()
        self.assertNotIn("post_process", ms)
        self.assertEqual(ms, ms_with())  # only that line went; every other byte is kept

    def test_character_reference_in_key(self):
        src = self.project("ref.3mf", ms=ms_with('    <metadata key="&#112;ost_process" value="x.exe"/>\n'))
        self.assertStripsClean(src, [MS])

    def test_part_level_and_layer_range_options(self):
        ms = ms_with().replace('<metadata key="name" value="Cube A"/>\n    </part>',
                               '<metadata key="name" value="Cube A"/>\n'
                               '      <metadata key="post_process" value="p.exe"/>\n    </part>')
        ranges = ('<?xml version="1.0" encoding="utf-8"?>\n<objects>\n <object id="1">\n'
                  '  <range min_z="0" max_z="1">\n   <option opt_key="post_process">r.exe</option>\n'
                  '   <option opt_key="layer_height">0.1</option>\n  </range>\n </object>\n</objects>\n')
        src = self.project("lr.3mf", ms=ms, extra=[("Metadata/layer_config_ranges.xml", ranges, False)])
        out, _ = self.assertStripsClean(src, [MS, "Metadata/layer_config_ranges.xml"])
        self.assertIn(b"layer_height", self.entries(out)["Metadata/layer_config_ranges.xml"])

    def test_doctype_entity_refused(self):
        ms = ms_with().replace('<?xml version="1.0" encoding="UTF-8"?>\n',
                               '<?xml version="1.0"?>\n<!DOCTYPE config [<!ENTITY pp "post_process">]>\n')
        self.assertRefused(self.project("dtd.3mf", ms=ms), "DOCTYPE")

    def test_billion_laughs_refused_fast(self):
        lol = "".join('<!ENTITY l%d "&l%d;&l%d;&l%d;&l%d;&l%d;&l%d;&l%d;&l%d;&l%d;&l%d;">' % ((i,) + (i - 1,) * 10)
                      for i in range(1, 10))
        ms = '<?xml version="1.0"?><!DOCTYPE c [<!ENTITY l0 "lol">%s]><c>&l9;</c>' % lol
        t = time.time()
        with self.assertRaises(bc.RefusedArchive):
            bc.open_project(self.project("lol.3mf", ms=ms))
        self.assertLess(time.time() - t, 2)

    def test_utf16_and_other_encodings_refused(self):
        doc = ms_with('    <metadata key="post_process" value="x.exe"/>\n').replace(' encoding="UTF-8"', "")
        self.assertRefused(self.project("u16.3mf", ms=doc.encode("utf-16")), "NUL bytes")  # with a BOM
        # expat follows UTF-16 even without a BOM when told UTF-8; the NUL bytes give it away
        self.assertRefused(self.project("u16le.3mf", ms=doc.encode("utf-16-le")), "NUL bytes")
        latin = doc.replace('<?xml version="1.0"?>', '<?xml version="1.0" encoding="ISO-8859-1"?>')
        self.assertRefused(self.project("l1.3mf", ms=latin.encode("latin-1")), "declares encoding")

    def test_post_process_only_in_object_name(self):
        src = self.project("name.3mf", ms=ms_with(name="post_process test"))
        found = self.findings(src)
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0].strippable)
        r = self.verify(src)
        self.assertEqual(r.returncode, 1)
        r = self.tune(src, self.path("o.3mf"), "--strip-post-process")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot remove post_process from Metadata/model_settings.config exactly", r.stderr)
        self.assertFalse(os.path.exists(self.path("o.3mf")))
        r = self.tune(src, self.path("o.3mf"))  # without --strip it warns and keeps the name
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WARNING", r.stdout)
        with self.assertRaises(bc.BambuError):
            slice_check.safe_project_copy(src, self.path("c.3mf"))
        self.assertFalse(os.path.exists(self.path("c.3mf")))

    def test_xml_comment_is_a_belt_finding(self):
        src = self.project("cm.3mf", ms=ms_with("    <!-- post_process -->\n"))
        found = self.findings(src)
        self.assertEqual(len(found), 1)
        self.assertIn("does not read", found[0].where)
        self.assertIn("mentions post_process", self.verify(src).stdout)
        with self.assertRaises(bc.BambuError):
            slice_check.safe_project_copy(src, self.path("c.3mf"))

    def test_other_metadata_is_scanned(self):
        src = self.project("si.3mf", extra=[("Metadata/slice_info.config",
                                             '<config><plate><metadata key="post_process" value="s.exe"/></plate>'
                                             '</config>', False),
                                            ("Metadata/notes.json", '{"post_process": "n.exe"}', False)])
        self.assertStripsClean(src, ["Metadata/slice_info.config", "Metadata/notes.json"])

    # ------------------------------------------------------------ entry names (NEW-L1)

    def test_unicode_format_and_separator_names_left_out(self):
        bad = ["Auxiliaries/a\u202eb.png", "3D/Objects/x\u200by.model", "Auxiliaries/l\u2028ine.txt"]
        ok = [("Auxiliaries/caf\u00e9.png", b"x", True)]
        src = self.project("uni.3mf", extra=[(n, b"x", True) for n in bad] + ok)
        r = self.verify(src)
        self.assertEqual(r.returncode, 1)
        self.assertIn("unsafe entry names", r.stdout)
        self.assertClean(r)
        for c in ("\u202e", "\u200b", "\u2028"):
            self.assertNotIn(c, r.stdout)
        out = self.path("uni_out.3mf")
        r = self.tune(fx.write_3mf(self.path("g.3mf"), gcode_file="Metadata/plate_1.gcode", extra=dict(
            [(n, "x") for n in bad] + [("Metadata/plate_1.gcode", "; gcode")])), out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertClean(r)
        names = self.entries(out)
        for n in bad:
            self.assertNotIn(n, names)
        self.assertIn("left out unsafe entry names", r.stdout)
        self.assertIn("Auxiliaries/caf\u00e9.png", self.entries(self.tune_ok(src)))

    def test_non_ascii_name_under_metadata_refused(self):
        src = self.project("na.3mf", extra=[("Metadata/caf\u00e9.config", b"{}", True)])
        self.assertRefused(src, "non-ASCII")

    def test_cp437_high_byte_name_under_metadata_refused(self):
        src = self.project("cp.3mf", extra=[(b"Metadata/process_settings_\x82.config", b'{"post_process": "x"}',
                                             False)])
        with zipfile.ZipFile(src) as z:
            self.assertEqual(z.infolist()[-1].flag_bits & 0x800, 0)
        self.assertRefused(src, "non-ASCII")

    # ------------------------------------------------------------ size and time (NEW-L2)

    def test_unclosed_tags_fail_fast(self):
        junk = '<object id="1">' * 20000  # 300 KB
        self.assertGreater(len(junk), 290000)
        t = time.time()
        with self.assertRaises(bc.RefusedArchive):
            bc.open_project(self.project("unc.3mf", ms='<config>' + junk))
        with self.assertRaises(bc.GeometryError):
            bc.Geometry({"3D/3dmodel.model": '<model><resources>' + junk})
        self.assertLess(time.time() - t, 2)
        r = self.verify(self.project("unc2.3mf", model='<model><resources>' + junk))
        self.assertEqual(r.returncode, 1)
        self.assertIn("geometry not checked", r.stdout)

    def test_settings_size_cap(self):
        src = self.project("big.3mf", ms=ms_with("<!-- %s -->\n" % ("x" * 5000)))
        with mock.patch.object(bc, "MAX_SETTINGS_BYTES", 4096):
            with self.assertRaises(bc.RefusedArchive) as cm:
                bc.open_project(src)
        self.assertIn("larger than", str(cm.exception))
        self.assertEqual(bc.MAX_SETTINGS_BYTES, 32 * 1024 * 1024)
        self.assertEqual(bc.MAX_TOTAL_BYTES, 1024 * 1024 * 1024)

    # ------------------------------------------------------------ writing

    def test_stale_gcode_any_case_dropped(self):
        src = self.project("st.3mf", extra=[("Metadata/plate_1.GCODE", "; old", False),
                                            ("Metadata/Plate_1.gcode.md5", "x", False)])
        out = self.tune_ok(src)
        names = self.entries(out)
        self.assertNotIn("Metadata/plate_1.GCODE", names)
        self.assertNotIn("Metadata/Plate_1.gcode.md5", names)

    def test_post_write_check_catches_a_mutated_strip(self):
        src = self.project("mut.3mf", extra=[("Metadata/process_settings_1.config", '{"post_process": "x"}', False),
                                             ("Metadata/filament_settings_1.config", '{"post_process": "y"}', False)])
        real = bc.strip_post_process

        def skips_one(index):
            out = real(index)
            out.pop(sorted(out)[0])
            return out
        dst = self.path("work", "copy.3mf")
        os.makedirs(os.path.dirname(dst))
        with mock.patch.object(bc, "strip_post_process", skips_one):
            with self.assertRaises(bc.BambuError) as cm:
                slice_check.safe_project_copy(src, dst)
        self.assertIn("internal check failed", str(cm.exception))
        self.assertEqual(os.listdir(os.path.dirname(dst)), [])
        with bc.open_project(src) as idx:
            with self.assertRaises(bc.BambuError):
                bc.atomic_write(dst, lambda tmp: (bc.write_project(idx, tmp, skips_one(idx)), bc.check_written(tmp)))
        self.assertEqual(os.listdir(os.path.dirname(dst)), [])

    def test_write_project_refuses_unknown_replace_key(self):
        with bc.open_project(fx.write_3mf(self.path("ok.3mf"))) as idx:
            with self.assertRaises(bc.BambuError):
                bc.write_project(idx, self.path("o.3mf"), {"metadata/nothing.config": b"{}"})

    def test_read_entry_takes_zipinfo_only(self):
        with bc.open_project(fx.write_3mf(self.path("ok.3mf"))) as idx:
            with self.assertRaises(TypeError):
                bc.read_entry(idx.zip, PS)

    def test_clean_project_is_not_refused_or_changed(self):
        src = fx.write_3mf(self.path("ok.3mf"))
        with bc.open_project(src) as idx:
            self.assertEqual((idx.findings, idx.unsafe), ([], []))
            self.assertEqual(bc.strip_post_process(idx), {})
        copy = self.path("copy.3mf")
        slice_check.safe_project_copy(src, copy)
        self.assertEqual(self.entries(copy), self.entries(src))
        out = self.path("out.3mf")
        r = fx.run("tune_project.py", src, out, "--strip-post-process")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("WARNING", r.stdout)
        self.assertEqual(self.entries(out)[MS], self.entries(src)[MS])

    # ------------------------------------------------------------ other inputs

    def test_slice_check_accepts_only_3mf_and_stl(self):
        obj = self.path("part.obj")
        with open(obj, "w") as f:
            f.write("v 0 0 0\n")
        r = fx.run("slice_check.py", obj, "--exe", sys.executable)
        self.assertEqual(r.returncode, 1)
        self.assertIn("only .3mf projects and .stl models", r.stderr)

    def test_tune_will_not_add_post_process_in_any_case(self):
        src = fx.write_3mf(self.path("ok.3mf"))
        for arg in (["--set", 'Post_Process=["calc.exe"]'], ["--object", "cube:POST_PROCESS=calc.exe"]):
            r = fx.run("tune_project.py", src, self.path("o.3mf"), "--allow-unknown-keys", *arg)
            self.assertEqual(r.returncode, 1, arg)
            self.assertIn("post", r.stderr)
        self.assertFalse(os.path.exists(self.path("o.3mf")))

    def test_settings_file_case_and_duplicate_keys(self):
        src = self.path("p.json")
        with open(src, "w") as f:
            f.write('{"Post_Process": ["a.exe"], "post_process": ["b.exe"], "post_process": []}')
        dst = self.path("p_copy.json")
        self.assertEqual(sorted(slice_check.safe_settings_copy(src, dst)), ["a.exe", "b.exe"])
        d = bc.load_json(dst)
        self.assertEqual((d["Post_Process"], d["post_process"]), ([], []))


PRUSA_CONFIG = "Metadata/Slic3r_PE.config"
PRUSA_MODEL = "Metadata/Slic3r_PE_model.config"
PRUSA_HEADER = "; generated by PrusaSlicer 2.7.1+win64 on 2026-11-20 at 10:00:00 UTC\n\n"


def prusa_config(post_process=""):
    return PRUSA_HEADER + "; layer_height = 0.2\n; post_process = %s\n; start_gcode = G28 ; home\n" % post_process


def flat_findings_ms(n):
    """model_settings.config with n object-level post_process settings."""
    return ms_with("".join('    <metadata key="post_process" value="x%d.exe"/>\n' % i for i in range(n)))


class AmendmentTest(Untrusted3mfBase):
    """The 2026-11-26 amendment: D9 resource limits, D10 Prusa entries, F3-F6 and the doc nits."""

    # ------------------------------------------------------------ D9: linear, with fixed caps

    def open_time(self, src, runs=3):
        best = None
        for _ in range(runs):
            t = time.perf_counter()
            bc.open_project(src).close()
            d = time.perf_counter() - t
            best = d if best is None else min(best, d)
        return best

    def test_flat_findings_are_linear_and_capped(self):
        small = self.project("f5k.3mf", ms=flat_findings_ms(5000))
        big = self.project("f20k.3mf", ms=flat_findings_ms(20000))
        t5, t20 = self.open_time(small), self.open_time(big)
        self.assertLess(t20, 2.0)
        self.assertLess(t20 / t5, 8, (t5, t20))  # linear is about 4, quadratic about 16
        tracemalloc.start()
        try:
            with bc.open_project(big) as idx:
                peak = tracemalloc.get_traced_memory()[1]
                self.assertEqual((len(idx.findings), idx.more_findings), (50, 19950))
                self.assertEqual(idx.strippable, {"metadata/model_settings.config": True})
                stripped = bc.strip_post_process(idx)
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 256 * 2 ** 20)
        self.assertNotIn(b"post_process", stripped["metadata/model_settings.config"])
        r = self.verify(big)
        self.assertIn("PROBLEM: ... and 19950 more post_process findings", r.stdout)
        self.assertEqual(json.loads(self.verify(big, "--json").stdout)["more_findings"], 19950)

    def test_deep_nesting_refused_fast(self):
        t = time.perf_counter()
        deep_xml = '<?xml version="1.0" encoding="UTF-8"?>\n<config>' + "<a>" * 5000 + "</a>" * 5000 + "</config>"
        with self.assertRaises(bc.RefusedArchive) as cm:
            bc.open_project(self.project("dx.3mf", ms=deep_xml))
        self.assertIn("nested more than 256 levels", str(cm.exception))
        deep_json = '{"a": ' + "[" * 5000 + "]" * 5000 + "}"
        with self.assertRaises(bc.RefusedArchive) as cm:
            bc.open_project(self.project("dj.3mf", ps=deep_json.encode()))
        self.assertIn("nested more than 256 levels", str(cm.exception))
        self.assertLess(time.perf_counter() - t, 1.0)

    def test_json_depth_counts_brackets_outside_strings_only(self):
        ok = '{"a": %s1%s, "s": "%s\\"]]"}' % ("[" * 255, "]" * 255, "[" * 5000)
        bc.parse_json_bytes(ok.encode(), "t")  # depth 256 exactly, and brackets in a string do not count
        with self.assertRaises(bc.BadEntry):
            bc.parse_json_bytes(('{"a": %s1%s}' % ("[" * 256, "]" * 256)).encode(), "t")
        bc.xml_tree(("<a>" * 256 + "</a>" * 256).encode(), "t")
        t = time.perf_counter()  # an unterminated string full of escaped quotes: the pre-scan stays linear
        with self.assertRaises(bc.BadEntry):
            bc.parse_json_bytes(b'{"a": ' + b'\\"' * 200000, "t")
        self.assertLess(time.perf_counter() - t, 1.0)
        with self.assertRaises(bc.BadEntry):
            bc.xml_tree(("<a>" * 257 + "</a>" * 257).encode(), "t")

    def test_settings_total_refused_from_the_directory(self):
        # 65 MB of settings and metadata, each entry under 32 MB, stored so the ratio check does not fire first
        part = b"{" + b" " * (33 * 2 ** 20 - 2) + b"}"
        ents = [fx.zip_entry(n, d) for n, d, _ in fx.project_entries(objects=ONE_OBJECT)]
        ents += [fx.zip_entry("Metadata/a.json", part, method=0), fx.zip_entry("Metadata/b.json", part, method=0)]
        src = fx.write_raw_zip_bytes(self.path("total.3mf"), ents)
        with mock.patch.object(bc, "read_entry", side_effect=AssertionError("an entry was read")):
            with self.assertRaises(bc.RefusedArchive) as cm:
                bc.open_project(src)
        self.assertIn("add up to 66 MB", str(cm.exception))
        self.assertEqual(bc.MAX_SETTINGS_TOTAL_BYTES, 64 * 2 ** 20)

    def test_trees_freed_after_detection(self):
        src = self.project("tf.3mf", extra=[
            ("Metadata/slice_info.config", '<config><plate><metadata key="post_process" value="s"/></plate></config>',
             False),
            ("Metadata/process_settings_1.config", '{"post_process": ["p"], "wall_loops": "2"}', False)])
        with bc.open_project(src) as idx:
            for norm, p in idx.parsed.items():
                if norm == "metadata/model_settings.config":
                    self.assertIs(p.value, idx.model_settings)
                else:
                    self.assertIsNone(p.value, norm)
            self.assertEqual(idx.presets[0][2]["wall_loops"], "2")
            stripped = bc.strip_post_process(idx)  # parses again
        self.assertEqual(sorted(stripped), ["metadata/process_settings_1.config", "metadata/slice_info.config"])
        self.assertStripsClean(src, ["Metadata/slice_info.config", "Metadata/process_settings_1.config"])

    # ------------------------------------------------------------ F3: no later check hides a found script

    def test_layout_error_does_not_hide_post_process(self):
        src = self.project("f3.3mf", ps=fx.project_settings(post_process=[EVIL], printable_area=["abc"]))
        r = self.verify(src)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertClean(r)
        probs = [x for x in r.stdout.splitlines() if x.startswith("PROBLEM: ")]
        self.assertTrue(probs[0].startswith("PROBLEM: post-processing script (post_process)"), probs)
        self.assertIn("run.exe", probs[0])
        self.assertTrue(any(x.startswith("PROBLEM: layout not checked: ") for x in probs[1:]), probs)
        d = json.loads(self.verify(src, "--json").stdout)
        self.assertEqual(len(d["post_process"]), 1)
        self.assertTrue(d["problems"][0].startswith("post-processing script"))
        self.assertTrue(any(x.startswith("layout not checked") for x in d["problems"]))

    def test_missing_main_model_does_not_hide_post_process(self):
        src = self.project("nomodel.3mf", ps=fx.project_settings(post_process=[EVIL]), drop=("3D/3dmodel.model",))
        r = self.verify(src)
        self.assertEqual(r.returncode, 1)
        self.assertIn("PROBLEM: post-processing script", r.stdout)
        self.assertIn("PROBLEM: geometry not checked: not a 3MF project", r.stdout)

    # ------------------------------------------------------------ F4: the ini strip never moves a line

    def test_ini_section_named_post_process_is_not_strippable(self):
        ini = "layer_height = 0.2\n[post_process]\nwall_loops = 3\n"
        src = self.project("f4a.3mf", extra=[("Metadata/print_setting_1.config", ini, False)])
        found = self.findings(src)
        self.assertEqual([f.strippable for f in found], [False])
        self.assertIn("section [post_process]", found[0].where)
        r = self.tune(src, self.path("o.3mf"), "--strip-post-process")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot remove post_process", r.stderr)
        self.assertFalse(os.path.exists(self.path("o.3mf")))
        with self.assertRaises(bc.BambuError):
            slice_check.safe_project_copy(src, self.path("c.3mf"))
        for other in ("note = run post_process\n", "my_post_process = 1\n"):
            src = self.project("f4b.3mf", extra=[("Metadata/print_setting_1.config", other, False)])
            self.assertEqual([f.strippable for f in self.findings(src)], [False], other)

    def test_ini_strip_inside_a_section_moves_nothing(self):
        ini = "layer_height = 0.2\n[extra]\npost_process = x.exe\nwall_loops = 3\n"
        src = self.project("f4c.3mf", extra=[("Metadata/print_setting_1.config", ini, False)])
        out, _ = self.assertStripsClean(src, ["Metadata/print_setting_1.config"])
        self.assertEqual(self.entries(out)["Metadata/print_setting_1.config"].decode(),
                         "layer_height = 0.2\n[extra]\nwall_loops = 3\n")
        with bc.open_project(out) as idx:
            self.assertEqual(idx.presets[0][2], {"layer_height": "0.2"})  # wall_loops stays in [extra]

    # ------------------------------------------------------------ F5: only ASCII whitespace is empty

    def test_unicode_space_values_are_findings(self):
        for v in (["　"], [" "], " "):
            src = self.project("f5.3mf", ps=fx.project_settings(post_process=v))
            self.assertEqual(len(self.findings(src)), 1, v)
        src = self.project("f5i.3mf", extra=[("Metadata/print_setting_1.config", "post_process = 　\n", False)])
        self.assertEqual(len(self.findings(src)), 1)
        src = self.project("f5e.3mf", ps=fx.project_settings(post_process=[" \t", "\x0b\x0c"]))
        self.assertEqual(self.findings(src), [])

    # ------------------------------------------------------------ F6: fixed modes on output

    def test_output_modes_are_fixed(self):
        ents = [fx.zip_entry(n, d) for n, d, _ in fx.project_entries(objects=ONE_OBJECT)]
        ents.append(fx.zip_entry("Auxiliaries/link.png", b"/etc/passwd", mode=0o120777))
        ents.append(fx.zip_entry("Auxiliaries/sub/", b"", method=0, mode=0o40777))
        src = fx.write_raw_zip_bytes(self.path("modes.3mf"), ents)
        for out in (self.tune_ok(src), self.copy(src)):
            with zipfile.ZipFile(out) as z:
                for i in z.infolist():
                    self.assertEqual(i.create_system, 3, i.filename)
                    want = (0o40755 << 16) | 0x10 if i.filename.endswith("/") else 0o100644 << 16
                    self.assertEqual(i.external_attr, want, i.filename)
                    self.assertEqual((i.extra, i.comment), (b"", b""), i.filename)

    def copy(self, src):
        dst = self.path("copy_%s" % os.path.basename(src))
        slice_check.safe_project_copy(src, dst)
        return dst

    # ------------------------------------------------------------ D10: the Prusa importer's entries

    def test_plain_prusa_project_verifies_clean(self):
        src = self.project("prusa.3mf", extra=[(PRUSA_CONFIG, prusa_config(), False)])
        self.assertEqual(self.findings(src), [])
        r = self.verify(src)
        self.assertEqual(r.returncode, 0, r.stdout)
        src = self.project("prusa_crlf.3mf", extra=[(PRUSA_CONFIG, prusa_config().replace("\n", "\r\n"), False)])
        self.assertEqual(self.findings(src), [])

    def test_prusa_config_script_is_stripped(self):
        src = self.project("prusa_pp.3mf", extra=[(PRUSA_CONFIG, prusa_config("/tmp/x.sh"), False)])
        found = self.findings(src)
        self.assertEqual([(f.value, f.where, f.strippable) for f in found], [("/tmp/x.sh", "(line 4)", True)])
        self.assertIn("PrusaSlicer runs it", self.verify(src).stdout)
        out, copy = self.assertStripsClean(src, [PRUSA_CONFIG])
        self.assertEqual(self.entries(copy)[PRUSA_CONFIG].decode(),
                         PRUSA_HEADER + "; layer_height = 0.2\n; start_gcode = G28 ; home\n")

    def test_prusa_config_other_mentions_are_not_strippable(self):
        for text in (prusa_config() + "; notes = see post_process\n", prusa_config() + "post_process\n"):
            src = self.project("prusa_o.3mf", extra=[(PRUSA_CONFIG, text, False)])
            self.assertEqual([f.strippable for f in self.findings(src)], [False], text)
            with self.assertRaises(bc.BambuError):
                slice_check.safe_project_copy(src, self.path("c.3mf"))

    def test_prusa_model_config_is_a_settings_entry(self):
        pm = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n<object id="1" instances_count="1">\n'
              ' <metadata type="object" key="post_process" value="x"/>\n'
              ' <metadata type="object" key="layer_height" value="0.2"/>\n'
              ' <volume firstid="0" lastid="11">\n  <metadata type="volume" key="name" value="Cube"/>\n'
              ' </volume>\n</object>\n</config>\n')
        src = self.project("pm.3mf", extra=[(PRUSA_MODEL, pm, False)])
        out, _ = self.assertStripsClean(src, [PRUSA_MODEL])
        self.assertEqual(self.entries(out)[PRUSA_MODEL].decode(),
                         pm.replace(' <metadata type="object" key="post_process" value="x"/>\n', ""))
        self.assertIn("object-level post_process", self.verify(src).stdout)

    def test_prusa_model_config_case_variant_refused(self):
        src = self.project("pmc.3mf", extra=[("Metadata/slic3r_pe_model.config", "<config/>", False)])
        self.assertRefused(src, "not spelled the way Bambu Studio writes it")

    # ------------------------------------------------------------ doc nit bss/where-leading-space

    def test_where_is_joined_with_one_space(self):
        src = self.project("w.3mf", ps=b'{"a": {"post_process": "x.exe"}}')
        found = self.findings(src)
        self.assertEqual(found[0].where, "(key a > post_process)")
        self.assertIn("in Metadata/project_settings.config (key a > post_process):", self.verify(src).stdout)
        ms = ms_with().replace('<metadata key="name" value="Cube A"/>\n    </part>',
                               '<metadata key="name" value="Cube A"/>\n'
                               '      <metadata key="post_process" value="p.exe"/>\n    </part>')
        r = self.verify(self.project("w2.3mf", ms=ms))
        self.assertIn("Metadata/model_settings.config (<metadata key=post_process> in part 1 of object 1)", r.stdout)
        r = self.tune(src, self.path("w_out.3mf"))
        self.assertIn("  in Metadata/project_settings.config (key a > post_process): x.exe", r.stdout)

    def test_long_json_path_is_cut(self):
        src = self.project("wl.3mf", ps=b'{"a": {"b": {"c": {"d": {"e": {"post_process": "x"}}}}}}')
        self.assertEqual(self.findings(src)[0].where, "(key ... > c > d > e > post_process)")


class XmlSpanTest(unittest.TestCase):
    """Byte spans from the one expat pass, which the strip and the tune edits cut on."""

    def spans(self, doc):
        d = doc.encode("utf-8")
        return [(e.tag, d[e.start:e.end].decode()) for e in bc.xml_tree(d, "t").iter() if e.tag != "#document"]

    def test_spans(self):
        self.assertEqual(self.spans('<a><b x="1"/></a>'), [("a", '<a><b x="1"/></a>'), ("b", '<b x="1"/>')])
        self.assertEqual(self.spans('<a><b></b ></a>'), [("a", "<a><b></b ></a>"), ("b", "<b></b >")])
        self.assertEqual(self.spans("<a>x/></a>")[0], ("a", "<a>x/></a>"))
        self.assertEqual(self.spans("<a><!-- c /--></a>")[0], ("a", "<a><!-- c /--></a>"))
        self.assertEqual(self.spans("<a><?p x/?></a>")[0], ("a", "<a><?p x/?></a>"))
        self.assertEqual(self.spans("\ufeff<a><b/></a>"), [("a", "<a><b/></a>"), ("b", "<b/>")])
        self.assertEqual(self.spans('<a k="&gt;/>"><b k=\'/>\'/></a>')[1], ("b", "<b k='/>'/>"))

    def test_drop_keeps_every_other_byte(self):
        d = b'<c>\n  <m key="post_process" value="x"/>\n  <m key="a" value="1"/>\n</c>\n'
        t = bc.xml_tree(d, "t")
        self.assertEqual(bc.drop_elements(d, [e for e in t.iter("m") if e.attrs["key"] == "post_process"]),
                         b'<c>\n  <m key="a" value="1"/>\n</c>\n')


if __name__ == "__main__":
    unittest.main()
