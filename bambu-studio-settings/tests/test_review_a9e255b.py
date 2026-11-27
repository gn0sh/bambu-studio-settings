"""Regression tests for the security review at a9e255b (docs/reviews/skills-share-a9e255b.md): B1 (ini and
G-code-comment lines charged to the budget, per-role memory), B2 (per-layer custom G-code noted), B3 (.model
element caps), B4 (bounded int parsing) and B5 (\\Z in the key regex). Problem families
untrusted-3mf/zip-resource-dos, untrusted-3mf/custom-gcode-coverage, untrusted-3mf/int-parse-dos and
bss/key-regex-dollar. Every fixture is generated here; none is committed."""
import json
import os
import random
import time
import tracemalloc
import unittest
from unittest import mock

import bss_fixtures as fx
import bambu_common as bc
from test_budget import BudgetBase, base_entries
from test_untrusted_3mf import ONE_OBJECT

MB = 2 ** 20
PEAK = 256 * MB            # the review's bound: one settings entry at 32 MB stays under this, traced
SIZE = bc.MAX_SETTINGS_BYTES - 64
PS = "Metadata/project_settings.config"
MS = "Metadata/model_settings.config"
MAIN = "3D/3dmodel.model"


def lines(unit, size, head=b"", tail=b""):
    """head + unit % 0, unit % 1, ... + spaces + tail: exactly size bytes (unit holds one %x or %06x)."""
    out, n, i = [head], len(head) + len(tail), 0
    while True:
        line = unit % i
        if n + len(line) > size:
            break
        out.append(line)
        n += len(line)
        i += 1
    out.append(b" " * (size - n) + tail)
    return b"".join(out)


def traced(fn):
    """(result or the BambuError raised, peak traced bytes, seconds) of fn()."""
    tracemalloc.start()
    t = time.perf_counter()
    try:
        try:
            r = fn()
        except bc.BambuError as e:
            r = e
        return r, tracemalloc.get_traced_memory()[1], time.perf_counter() - t
    finally:
        tracemalloc.stop()


def open_and_detect(src):
    """What verify, tune and the analyzer all do first: open_project, the detector, then the geometry."""
    def run():
        with bc.open_project(src) as idx:
            idx.geometry()
            return len(idx.findings) + idx.more_findings
    return run


# The cheapest line shape of each settings role (ADR 0006 D2, D10), 32 MB each: the most lines, keys, sections,
# strings or elements one entry can hold. (role, entry, data, refused): refused is the reason's text, or None
# when the entry must be read in full.
SHAPES = {
    "project json scalars": (PS, lambda: b"[" + b"0," * (SIZE // 2 - 1) + b"0]", "strings"),
    "project json pairs": (PS, lambda: lines(b'"%x":0,', SIZE, b"{", b'"z":0}'), "strings"),
    "project json pairs under the cap": (PS, lambda: lines(b'"%06x":"' + b"v" * 56 + b'",', SIZE, b"{", b'"z":0}'),
                                         None),
    "preset json pairs": ("Metadata/process_settings_1.config", lambda: lines(b'"%x":"",', SIZE, b"{", b'"z":0}'),
                          "strings"),
    "preset ini pairs": ("Metadata/print_setting_1.config", lambda: lines(b"%x=\n", SIZE), "strings"),
    "preset ini sections": ("Metadata/print_setting_1.config", lambda: lines(b"[%x]\n", SIZE), "strings"),
    "preset ini pairs in a section": ("Metadata/print_setting_1.config", lambda: lines(b"%x=\n", SIZE, b"[s]\n"),
                                      "strings"),
    "preset ini pairs naming post_process": ("Metadata/print_setting_1.config",
                                             lambda: lines(b"post_process%x=\n", SIZE), "strings"),
    "preset ini comments": ("Metadata/print_setting_1.config", lambda: b";\n" * (SIZE // 2), None),
    "preset ini pairs under the cap": ("Metadata/print_setting_1.config",
                                       lambda: lines(b"%06x=" + b"v" * 60 + b"\n", SIZE), None),
    "scan gcode-comment blank lines": ("Metadata/Slic3r_PE.config", lambda: b"\n" * SIZE, None),
    "scan gcode-comment post_process lines": ("Metadata/Slic3r_PE.config", lambda: lines(b"post_process%x\n", SIZE),
                                              "strings"),
    "model xml elements": (MS, lambda: b"<c>" + b"<a/>" * ((SIZE - 7) // 4) + b"</c>", "XML elements"),
    # 900 attributes per tag, so each tag is under MAX_TAG_BYTES (N1; it was 9,000 per tag x 200)
    "model xml attributes": (MS, lambda: b"<c>" + (b"<a" + b"".join(b' a%x=""' % i for i in range(900)) + b"/>")
                             * 2000 + b"</c>", "strings"),
    "model xml one tag of attributes": (MS, lambda: lines(b' a%x=""', SIZE, b"<c", b"/>"), "markup longer than"),
    "model xml under the cap": (MS, lambda: lines(b'<a b="%06x' + b"v" * 150 + b'" c="" d="" e=""/>', SIZE,
                                                  b"<c>", b"</c>"), None),
    "prusa-model xml elements": ("Metadata/Slic3r_PE_model.config", lambda: b"<c>" + b"<a/>" * ((SIZE - 7) // 4)
                                 + b"</c>", "XML elements"),
    "scan xml text nodes": ("Metadata/other.xml", lambda: b"<c>" + b"<a>x</a>" * ((SIZE - 7) // 8) + b"</c>",
                            "XML elements"),
    "scan json scalars": ("Metadata/other.json", lambda: b"[" + b"0," * (SIZE // 2 - 1) + b"0]", "strings"),
    "scan sniffed raw text": ("Metadata/other.config", lambda: b"x" * SIZE, None),
}


class SettingsRoleMemoryTest(BudgetBase):
    """B1: each settings role at 32 MB of its cheapest line shape stays under 256 MB traced, read or refused."""

    def test_each_role_at_32_mb(self):
        for label, (name, gen, refused) in SHAPES.items():
            with self.subTest(label):
                ents = [fx.zip_entry(n, d) for n, d, _ in fx.project_entries(objects=ONE_OBJECT) if n != name]
                src = self.raw_bytes("shape.3mf", ents + [fx.zip_entry(name, gen(), method=0)])
                r, peak, secs = traced(open_and_detect(src))
                os.remove(src)
                self.assertLess(peak, PEAK, "%s: %d MB" % (label, peak // MB))
                self.assertLess(secs, 60)
                if refused is None:
                    self.assertNotIsInstance(r, BaseException, "%s: %s" % (label, r))
                else:
                    self.assertIsInstance(r, bc.RefusedArchive, label)
                    self.assertIn(refused, str(r))

    def test_strip_of_many_post_process_lines(self):
        # under the cap, every line a finding: the copy without them is built without a list of lines
        gc = lines(b"; post_process = /tmp/%06x\n", SIZE // 2)
        ini = lines(b"\n; post_process %x\n", SIZE // 2)  # comments are not charged; blank lines are kept
        for label, name, data in (("gcode-comment", "Metadata/Slic3r_PE.config", gc),
                                  ("ini", "Metadata/print_setting_1.config", ini)):
            with self.subTest(label):
                src = self.raw_bytes("strip.3mf", base_entries() + [fx.zip_entry(name, data, method=0)])

                def run():
                    with bc.open_project(src) as idx:
                        self.assertGreater(len(idx.findings) + idx.more_findings, 0)
                        return bc.strip_post_process(idx)
                out, peak, secs = traced(run)
                self.assertNotIsInstance(out, BaseException, out)
                self.assertLess(peak, PEAK, "%s: %d MB" % (label, peak // MB))
                new = out[bc.norm_name(name)]
                self.assertLessEqual(set(new), set(b" \n"))  # only the blank lines and the filler are left
                self.assertEqual(new.count(b"\n"), data.count(b"\n") // 2 if label == "ini" else 0)


class ReviewB1FixturesTest(BudgetBase):
    """The review's B1 fixtures, rebuilt: 185 KB of deflated Slic3r_PE.config lines made 3.66 GB, and two 32 MB
    ini presets made 1.6 GB."""

    def gc32(self, name):
        rnd, buf, n = random.Random(7), bytearray(), SIZE
        while len(buf) < n:
            buf += b"\n" * int(rnd.expovariate(0.003))
            buf += b"a\n"
        ents = [fx.zip_entry(n_, d) for n_, d, _ in fx.project_entries(objects=ONE_OBJECT)]
        return self.raw_bytes(name, ents + [fx.zip_entry("Metadata/Slic3r_PE.config", bytes(buf[:n]))])

    def test_gcode_comment_lines(self):
        src = self.gc32("gc32.3mf")
        self.assertLess(os.path.getsize(src), 400 * 1024)
        r, peak, secs = traced(open_and_detect(src))
        self.assertEqual(r, 0)
        self.assertLess(peak, PEAK)
        self.assertLess(secs, 10)
        r = self.verify(src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_two_ini_presets(self):
        # 32 MB and just under it, so the settings total stays under its 64 MB cap and the parser has to stop it
        ents = base_entries() + [fx.zip_entry("Metadata/print_setting_%d.config" % i, lines(b"%x=\n", size), method=0)
                                 for i, size in ((1, SIZE), (2, SIZE - 256 * 1024))]
        src = self.raw_bytes("ini2x32.3mf", ents)
        r, peak, secs = traced(open_and_detect(src))
        self.assertIsInstance(r, bc.RefusedArchive)
        self.assertIn("more than 1000000 strings", str(r))
        self.assertLess(peak, PEAK)
        self.assertLess(secs, 30)


class IniAndGcodeLinesTest(BudgetBase):
    """B1: the regex line walk reads every line the way the old split-every-line reader did."""

    @staticmethod
    def old_strip(text, drop_line):
        """The reader before B1: split every line, drop the ones drop_line(line) names, join the rest."""
        return "\n".join(x for x in text.split("\n") if not drop_line(x)).encode("utf-8")

    def test_line_numbers_charges_and_kept_lines(self):
        text = (b"\n; a comment\n\r\n  # post_process in a comment\n[sec]\nb = 1\r\n[post_process_sec]\n"
                b"\n\x0b c = d \n post_process = run.exe\n")
        budget = bc.Budget()
        v = bc.parse_ini(text, "x.config", budget)
        self.assertEqual(list(v), [("post_process_sec", "post_process", "run.exe", 10)])
        self.assertEqual(list(v.section_names()), [("post_process_sec", 7)])
        self.assertEqual(v.top, {})
        self.assertEqual(budget.used["strings"], 2 * 5)  # 3 pairs and 2 sections, two strings each
        v = bc.parse_ini(b"a=1\nb = 2 \n[s]\na=3\n", "x.config")
        self.assertEqual((list(v), v.top), ([], {"a": "1", "b": "2"}))
        for bad, why in ((b"a=1\n\n\na=2\n", "line 4 repeats key a"), (b"[s]\n[t]\n\n[s]\n", "line 4 repeats section"),
                         (b"[s]\na=1\n[t]\na=1\n[s2]\n;\n a \n", "line 7 has no '='"), (b"=1\n", "empty key"),
                         (b"[s\n", "without ']'")):
            with self.assertRaises(bc.BadEntry) as cm:
                bc.parse_ini(bad, "x.config")
            self.assertIn(why, str(cm.exception))
        budget = bc.Budget()
        g = bc.parse_gcode_comment(b"; generated\n\n; post_process = a\r\n;x = 1\n\ttext post_process\n", "g", budget)
        self.assertEqual(list(g), [(3, "post_process", "a"), (5, None, "text post_process")])
        self.assertEqual(budget.used["strings"], 2)

    def test_strip_matches_the_old_reader(self):
        rnd = random.Random(5)
        pieces = ["", " ", "\r", "a = 1", "; note", "; post_process = x", "post_process = y", "# POST_PROCESS",
                  "k = v", "[s]"]
        for _ in range(300):
            text = "\n".join(rnd.choice(pieces) for _ in range(rnd.randrange(0, 8)))
            ini = text.replace("[s]", "")  # one section header at most keeps every key unique
            ini = "\n".join(x if x not in ("a = 1", "k = v") else "%s%d = 1" % (x[0], i)
                            for i, x in enumerate(ini.split("\n")))
            data = ini.encode()
            if not data or data.count(b"post_process = y") > 1:  # a repeated key is refused, not stripped
                continue
            p = bc.parse_settings_bytes(data, "Metadata/print_setting_1.config", "ini")

            def ini_drop(x):
                s = x.strip(bc.ASCII_WS)
                return bool(s) and "post_process" in s.lower() and (s[0] in ";#" or s.split("=")[0].strip(
                    bc.ASCII_WS).lower() == "post_process")
            self.assertEqual(bc.strip_parsed(p), self.old_strip(ini, ini_drop), repr(ini))
            gc = "\n".join(x for x in text.split("\n") if x in ("; post_process = x", "", " ", "\r", "; note"))
            if not gc:
                continue
            p = bc.parse_settings_bytes(gc.encode(), "Metadata/Slic3r_PE.config", "gcode-comment")
            self.assertEqual(bc.strip_parsed(p), self.old_strip(gc, lambda x: "post_process" in x), repr(gc))

    def test_json_prescan_stops_at_the_cap(self):
        budget = bc.Budget()
        budget.used["strings"] = bc.MAX_STRINGS - 3
        bc.parse_json_bytes(b'{"a": [1, true]}', "j", budget)
        self.assertEqual(budget.used["strings"], bc.MAX_STRINGS)
        with self.assertRaises(bc.BadEntry):
            bc.parse_json_bytes(b'[0]', "j", budget)
        with self.assertRaises(bc.BadEntry) as cm:
            bc.parse_json_bytes(b"[" * 257 + b"]" * 257, "j")
        self.assertIn("nested more than 256", str(cm.exception))
        bc.parse_json_bytes(b"[" * 256 + b"]" * 256, "j")


class LayerGcodeTest(BudgetBase):
    """B2: per-layer custom G-code (Metadata/custom_gcode_per_layer.xml) is a verify note: a count and the
    first three, sanitized, so SKILL.md rule 8 ("notes custom G-code") stays true."""

    def layers(self, n, text=fx.INJECT):
        rows = "".join('<layer top_z="%.1f" type="4" extruder="1" color="" extra="%s" gcode="ignored"/>\n'
                       % (0.2 * (i + 1), fx.xml_value(text) if i == 0 else "M117 layer %d" % i) for i in range(n))
        return ('<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer><plate><plate_info id="1"/>\n%s'
                '<layer top_z="9.0" type="1" extruder="1" color="" extra="" gcode="M400 U1"/>\n'
                '<mode value="SingleExtruder"/></plate></custom_gcodes_per_layer>\n' % rows)

    def test_noted_with_count_and_first_three(self):
        src = self.project("layers.3mf", extra=[("Metadata/custom_gcode_per_layer.xml", self.layers(5), False)])
        with bc.open_project(src) as idx:
            (name, count, first), = idx.layer_gcode
        self.assertEqual((name, count, len(first)), ("Metadata/custom_gcode_per_layer.xml", 5, 3))
        r = self.verify(src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("note: custom G-code at 5 layer(s) in Metadata/custom_gcode_per_layer.xml: z=0.2: Cube", r.stdout)
        self.assertIn("z=0.6: M117 layer 2; ... - it runs on the printer", r.stdout)
        self.assertNotIn("layer 3", r.stdout)
        self.assertClean(r)
        d = json.loads(self.verify(src, "--json").stdout)
        self.assertTrue(any("custom G-code at 5 layer(s)" in n for n in d["notes"]), d["notes"])

    def test_old_format_and_prusa_file(self):
        old = '<custom_gcodes_per_layer><plate><layer top_z="1.0" extruder="1" color="" gcode="G4 S5"/>' \
              '</plate></custom_gcodes_per_layer>'
        prusa = '<custom_gcodes_per_print_z><code print_z="2.0" type="4" extruder="1" color="" extra="M300" ' \
                'gcode="M300"/></custom_gcodes_per_print_z>'
        src = self.project("old.3mf", extra=[("Metadata/custom_gcode_per_layer.xml", old, False),
                                             ("Metadata/Prusa_Slicer_custom_gcode_per_print_z.xml", prusa, False)])
        with bc.open_project(src) as idx:
            self.assertEqual([(c, f) for _, c, f in idx.layer_gcode], [(1, [("1.0", "G4 S5")]), (1, [("2.0", "M300")])])

    def test_pause_and_colour_change_alone_are_not_noted(self):
        src = self.project("pause.3mf", extra=[("Metadata/custom_gcode_per_layer.xml", self.layers(0), False)])
        with bc.open_project(src) as idx:
            self.assertEqual(idx.layer_gcode, [])
        self.assertNotIn("layer(s)", self.verify(src).stdout)


class ModelElementCapsTest(BudgetBase):
    """B3: Geometry counts every .model element (and every one but vertices and triangles again) and the
    characters it keeps, against the archive budget; and no XML tag may carry MAX_ATTRIBUTES attributes."""

    def model_project(self, name, model):
        ents = [fx.zip_entry(n, d, method=0) for n, d, _ in fx.project_entries(objects=ONE_OBJECT, model=model)]
        return self.raw_bytes(name, ents)

    def test_other_elements_at_the_real_cap(self):
        main = fx.model_xml([("1", fx.mesh_xml())], [("1", "1 0 0 0 1 0 0 0 1 50 50 0")]).encode()
        k = main.index(b"</model>")
        src = self.model_project("geo.3mf", main[:k] + b"<a/>" * bc.MAX_MODEL_OTHER + main[k:])
        t = time.perf_counter()
        with bc.open_project(src) as idx:
            with self.assertRaises(bc.GeometryError) as cm:
                idx.geometry()
        self.assertIn("more than 1000000 elements other than vertices and triangles", str(cm.exception))
        self.assertLess(time.perf_counter() - t, 10)
        r = self.verify(src)
        self.assertIn("geometry not checked", r.stdout)
        self.assertClean(r)

    def test_all_elements_and_kept_characters(self):
        main = fx.model_xml([("1", fx.mesh_xml())], [("1", "1 0 0 0 1 0 0 0 1 50 50 0")])
        src = self.model_project("cube.3mf", main)
        with bc.open_project(src) as idx:
            geo = idx.geometry()
            used = dict(idx.budget.used)
        self.assertEqual(geo.stored, 8)
        n = used["model_elements"]
        with mock.patch.object(bc, "MAX_MODEL_ELEMENTS", n), bc.open_project(src) as idx:
            idx.geometry()
        with mock.patch.object(bc, "MAX_MODEL_ELEMENTS", n - 1), bc.open_project(src) as idx:
            with self.assertRaises(bc.GeometryError) as cm:
                idx.geometry()
        self.assertIn("elements in its .model files", str(cm.exception))
        long_name = fx.model_xml([("1", fx.mesh_xml())], [("1", "1 0 0 0 1 0 0 0 1 50 50 0")]).replace(
            '<object id="1"', '<object id="1" name="%s"' % ("n" * 5000))
        src = self.model_project("name.3mf", long_name)
        with mock.patch.object(bc, "MAX_MODEL_CHARS", 4000), bc.open_project(src) as idx:
            idx.geometry()  # names are kept in triangle mode only
            with self.assertRaises(bc.GeometryError) as cm:
                idx.geometry(triangles=True)
        self.assertIn("characters of ids, names, paths and transforms", str(cm.exception))

    def test_attributes_per_tag(self):
        """Attributes per tag are bounded by the held-input cap (ADR 0006 deviation 54: the '=' scan is gone):
        at most MAX_TAG_BYTES + XML_CHUNK of one tag, so about 20,000 attributes, never millions."""
        n = 10000
        ok = b"<c" + b"".join(b' a%d=""' % i for i in range(n)) + b"/>"
        # 10,000 attributes need 79 KB: over MAX_TAG_BYTES but under MAX_TAG_BYTES + XML_CHUNK, so read or refused
        # by where the tag falls (ADR 0006, expat input invariant); patched, it is read wherever it falls
        with mock.patch.object(bc, "MAX_TAG_BYTES", 2 * len(ok)):
            self.assertEqual(len(bc.xml_tree(ok, "x").children[0].attrs), n)
        long_tag = b"<c" + b"".join(b' a%d="0123456789"' % i for i in range(n)) + b"/>"
        self.assertGreater(len(long_tag), bc.MAX_TAG_BYTES + bc.XML_CHUNK)
        for data in (long_tag, b"<d>" + b"x" * (4 * MB - 100) + long_tag + b"</d>"):
            with self.assertRaises(bc.BadEntry) as cm:
                bc.xml_tree(data, "x")  # the same attributes with longer values: refused at the default
            self.assertIn("markup longer than", str(cm.exception))
        many = b"<d>" + b'<e a="" b=""/>' * n + b"</d>"
        bc.xml_tree(many, "x")

    def test_equals_signs_in_text_read(self):
        """untrusted-3mf/over-refusal/eq-in-text: the old scan refused text with 10,000 '=' before the next '<'."""
        for text in (b"=" * 10000, b"a = b; " * 10000, b"=" * (4 * MB)):
            root = bc.xml_tree(b"<r>" + text + b"</r>", "x").children[0]
            self.assertEqual(root.text, text.decode())


class IntParsingTest(unittest.TestCase):
    """B4: digit strings are length-checked before int() (quadratic on 3.8-3.10 before their patch releases)."""

    def test_id_re(self):
        for good in ("1", "123456789"):
            self.assertTrue(bc.ID_RE.match(good), good)
        for bad in ("", "1234567890", "1\n", "١", " 1", "1 ", "9" * 5000):
            self.assertFalse(bc.ID_RE.match(bad), repr(bad))

    def test_to_int(self):
        self.assertEqual(bc.to_int(" 42 ", 0), 42)
        self.assertEqual(bc.to_int("9" * 18, 0), 10 ** 18 - 1)
        self.assertEqual(bc.to_int("9" * 19, 7), 7)
        t = time.perf_counter()
        self.assertEqual(bc.to_int("9" * 1000000, 7), 7)
        self.assertLess(time.perf_counter() - t, 0.5)

    def test_triangle_indices(self):
        geo = bc.Geometry(triangles=True)
        mesh = '<vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/>' \
               '</vertices><triangles><triangle v1="0" v2="1" v3="%s"/></triangles>'
        geo.add(MAIN, '<model><resources><object id="1"><mesh>%s</mesh></object></resources></model>' % (mesh % "2"))
        geo = bc.Geometry(triangles=True)
        with self.assertRaises(bc.GeometryError) as cm:
            geo.add(MAIN, '<model><resources><object id="1"><mesh>%s</mesh></object></resources></model>'
                    % (mesh % ("0" * 18 + "2")))
        self.assertIn("without integer v1, v2, v3", str(cm.exception))


class KeyRegexTest(unittest.TestCase):
    """B5: a setting key ends at \\Z, so 'wall_loops\\n' is not a key."""

    def test_trailing_newline_is_not_a_key(self):
        self.assertTrue(bc.SETTING_KEY_RE.match("wall_loops"))
        self.assertFalse(bc.SETTING_KEY_RE.match("wall_loops\n"))


if __name__ == "__main__":
    unittest.main()
