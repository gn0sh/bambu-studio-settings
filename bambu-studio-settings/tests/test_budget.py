"""ADR 0006 D9 (restated 2026-11-26) and D8 Z8: the whole-archive budget, exact-length inflate, and how a
refusal is reported (security review at 95d4961: M1, L1, L2, L3).

Problem families untrusted-3mf/zip-resource-dos, untrusted-3mf/reader-crash, untrusted-3mf/container-parity and
untrusted-3mf/refusal-path. Each M1 shape is tested twice: as the review built it (deflated, under the 1 MB
per-entry ratio floor: refused by a ratio from the directory), and stored, so the ratio passes and the count
cap has to stop it; both with time and peak-memory bounds."""
import contextlib
import io
import json
import os
import sys
import time
import tracemalloc
import unittest
import zipfile
import zlib
from unittest import mock

import bss_fixtures as fx
import bambu_common as bc
import verify_project
from test_untrusted_3mf import EVIL, ONE_OBJECT, PS, Untrusted3mfBase

MB = 2 ** 20
MAIN = "3D/3dmodel.model"


def base_entries(**kw):
    return [fx.zip_entry(n, d, **kw) for n, d, _ in fx.project_entries(objects=ONE_OBJECT)]


def filler(unit, size, head=b"", tail=b""):
    """head + unit repeated + tail, exactly size bytes (unit must divide what is left)."""
    n, r = divmod(size - len(head) - len(tail), len(unit))
    assert r == 0, (size, unit)
    return head + unit * n + tail


def measure(fn):
    """(seconds, peak traced bytes) of fn(), timed without tracemalloc (it slows allocation down)."""
    t = time.perf_counter()
    fn()
    secs = time.perf_counter() - t
    tracemalloc.start()
    try:
        fn()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    return secs, peak


class BudgetBase(Untrusted3mfBase):
    def raw_bytes(self, name, ents):
        return fx.write_raw_zip_bytes(self.path(name), ents)

    def refused_reason(self, src):
        with self.assertRaises(bc.RefusedArchive) as cm:
            bc.open_project(src).close()
        return cm.exception.reason

    def refuse_measured(self, src, why, secs, peak_mb):
        """open_project refuses src with why, within secs and peak_mb of traced memory."""
        def run():
            self.assertIn(why, self.refused_reason(src))
        t, peak = measure(run)
        self.assertLess(t, secs)
        self.assertLess(peak, peak_mb * MB)

    def base_used(self):
        """The budget the standard one-object project uses by itself."""
        with bc.open_project(self.raw_bytes("base.3mf", base_entries())) as idx:
            return dict(idx.budget.used)

    def verify_inprocess(self, *args):
        """(exit code, stdout) of verify_project.main() run in this process (so a test can patch the reader)."""
        out = io.StringIO()
        env = dict(os.environ)
        env.pop("BSS_DEBUG", None)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "argv", ["verify_project.py"] + [str(a) for a in args]), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                verify_project.main()
        return cm.exception.code, out.getvalue()


class M1ShapesTest(BudgetBase):
    """The three M1 fixtures from the security review at 95d4961."""

    def test_metadata_entries_under_the_ratio_floor(self):
        # 63 x 1 MB of <a/> (each exactly at the 1 MB floor, so the per-entry ratio never looked) in about 70 KB
        one = filler(b"<a/>", MB, b"<c> ", b"</c>")
        self.assertEqual(len(one), MB)
        comp = fx.deflate(one)
        ents = base_entries() + [fx.zip_entry("Metadata/m%02d.xml" % i, one, comp=comp) for i in range(63)]
        src = self.raw_bytes("m1_meta.3mf", ents)
        self.assertLess(os.path.getsize(src), 100 * 1024)
        self.refuse_measured(src, "expand", 1.0, 128)
        self.assertRefused(src, "this looks like a zip bomb")
        # with 1 MB of incompressible data next to them, the archive as a whole passes its ratio, and the one
        # ratio for settings and metadata together still refuses, from the directory
        pad = fx.zip_entry("Auxiliaries/pad.bin", os.urandom(MB), method=0)
        src = self.raw_bytes("m1_meta_pad.3mf", ents + [pad])
        with mock.patch.object(bc, "read_entry", side_effect=AssertionError("an entry was read")):
            self.refuse_measured(src, "settings and metadata entries together expand", 1.0, 128)

    def test_metadata_entries_stored_hit_the_element_cap(self):
        # the same shape stored: every ratio passes, and the archive-wide element count stops it in the first
        # entry; the tree is dropped, so the peak is one entry's tree, not 63
        one = filler(b"<a/>", MB, b"<c> ", b"</c>")
        ents = base_entries() + [fx.zip_entry("Metadata/m%02d.xml" % i, one, method=0) for i in range(63)]
        src = self.raw_bytes("m1_meta_stored.3mf", ents)
        self.refuse_measured(src, "more than 200000 XML elements", 2.0, 128)

    def test_json_containers(self):
        # the review's 3.5 GB of JSON containers: deflated, refused by a ratio; stored, by MAX_JSON_CONTAINERS
        # before json.loads builds them
        one = filler(b"[],", MB, b"[", b"[]]")
        comp = fx.deflate(one)
        ents = [fx.zip_entry("Metadata/j%02d.json" % i, one, comp=comp) for i in range(40)]
        self.refuse_measured(self.raw_bytes("m1_json.3mf", base_entries() + ents), "expand", 1.0, 128)
        ents = [fx.zip_entry("Metadata/j%02d.json" % i, one, method=0) for i in range(40)]
        src = self.raw_bytes("m1_json_stored.3mf", base_entries() + ents)
        real = json.loads

        def loads(text, **kw):
            if len(text) > 100000:
                raise AssertionError("json.loads ran on a document past the budget")
            return real(text, **kw)

        with mock.patch.object(bc.json, "loads", side_effect=loads):
            self.refuse_measured(src, "more than 200000 JSON objects and arrays", 2.0, 128)

    def test_model_files_of_items(self):
        # the review's 400 .model files of <item/> (4.4 GB of attribute dicts, 149,776 output lines)
        main = fx.model_xml([("1", fx.mesh_xml())], [("1", "1 0 0 0 1 0 0 0 1 50 50 0")]).encode()
        one = filler(b"<item/>", 30 + 7 * 149791, b"<model><build>", b"</build></model>")  # just under 1 MB
        comp = fx.deflate(one)
        ents = [fx.zip_entry(n, d) for n, d, _ in fx.project_entries(objects=ONE_OBJECT, model=main)]
        big = ents + [fx.zip_entry("3D/Objects/o%03d.model" % i, one, comp=comp) for i in range(400)]
        self.refuse_measured(self.raw_bytes("m1_items.3mf", big), "entries together expand", 1.0, 128)
        # stored and smaller (so the ratio passes): items outside the main build are not stored at all
        small = filler(b'<item objectid="1"/>', 20 * 1000 + 30, b"<model><build>", b"</build></model>")
        ents += [fx.zip_entry("3D/Objects/o%03d.model" % i, small, method=0) for i in range(400)]
        src = self.raw_bytes("m1_items_stored.3mf", ents)

        def run():
            with bc.open_project(src) as idx:
                geo = idx.geometry()
            self.assertEqual(list(geo.items), ["3d/3dmodel.model"])
            self.assertEqual(geo.items["3d/3dmodel.model"],
                             [{"objectid": "1", "transform": "1 0 0 0 1 0 0 0 1 50 50 0"}])
        t, peak = measure(run)
        self.assertLess(t, 2.0)
        self.assertLess(peak, 64 * MB)
        r = self.verify(src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(len(r.stdout.splitlines()), 20)


class ArchiveCapsTest(BudgetBase):
    """Each archive-wide cap: exactly at the cap is read, one past it is refused."""

    def test_xml_elements_across_entries(self):
        used = self.base_used()["xml_elements"]
        left = bc.MAX_XML_ELEMENTS - used

        def project(name, n):
            per, extra = divmod(n, 5)
            ents = []
            for i in range(5):
                k = per + (extra if i == 0 else 0)  # k elements: <c> plus k - 1 <a/>
                ents.append(fx.zip_entry("Metadata/x%d.xml" % i, b"<c>" + b"<a/>" * (k - 1) + b"</c>", method=0))
            return self.raw_bytes(name, base_entries() + ents)

        with bc.open_project(project("at.3mf", left)) as idx:
            self.assertEqual(idx.budget.used["xml_elements"], bc.MAX_XML_ELEMENTS)
        self.assertIn("more than 200000 XML elements", self.refused_reason(project("over.3mf", left + 1)))

    def test_json_containers_and_strings_across_entries(self):
        used = self.base_used()

        def project(name, n, unit):
            per, extra = divmod(n, 4)
            ents = []
            for i in range(4):
                k = per + (extra if i == 0 else 0)
                if unit == b'""':  # k strings (and one container)
                    body = b"[" + b",".join([unit] * k) + b"]"
                else:  # k containers
                    body = b"[" + b",".join([unit] * (k - 1)) + b"]"
                ents.append(fx.zip_entry("Metadata/j%d.json" % i, body, method=0))
            return self.raw_bytes(name, base_entries() + ents)

        left = bc.MAX_JSON_CONTAINERS - used["json_containers"]
        with bc.open_project(project("c_at.3mf", left, b"[]")) as idx:
            self.assertEqual(idx.budget.used["json_containers"], bc.MAX_JSON_CONTAINERS)
        self.assertIn("more than 200000 JSON objects and arrays",
                      self.refused_reason(project("c_over.3mf", left + 1, b"[]")))
        left = bc.MAX_STRINGS - used["strings"]
        with bc.open_project(project("s_at.3mf", left, b'""')) as idx:
            self.assertEqual(idx.budget.used["strings"], bc.MAX_STRINGS)
        self.assertIn("more than 1000000 strings", self.refused_reason(project("s_over.3mf", left + 1, b'""')))

    def test_numbers_count_as_values(self):
        b = bc.Budget()
        bc.parse_json_bytes(b'{"a": [1, 2.5e3, true, null, "x"], "b": {}}', "t", b)
        self.assertEqual((b.used["strings"], b.used["json_containers"]), (7, 3))  # 3 strings, 4 other values

    def test_build_items(self):
        mesh = fx.mesh_xml()

        def model(n):
            return fx.model_xml([("1", mesh)], [("1", None)] * n)

        g = bc.Geometry({MAIN: model(bc.MAX_BUILD_ITEMS)})
        self.assertEqual(len(g.items["3d/3dmodel.model"]), bc.MAX_BUILD_ITEMS)
        with self.assertRaises(bc.GeometryError) as cm:
            bc.Geometry({MAIN: model(bc.MAX_BUILD_ITEMS + 1)})
        self.assertIn("more than 10000 build items", str(cm.exception))
        src = self.project("items.3mf", model=model(bc.MAX_BUILD_ITEMS + 1))
        r = self.verify(src)
        self.assertEqual(r.returncode, 1)
        self.assertIn("PROBLEM: geometry not checked: the project has more than 10000 build items", r.stdout)
        self.assertLess(len(r.stdout.splitlines()), 20)

    def test_items_keep_three_attributes_and_only_the_main_build(self):
        model = ('<model><resources><object id="1"><mesh/></object></resources><build>'
                 '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0" printable="0" a="x" b="y"/></build>'
                 '<item objectid="9"/></model>')
        g = bc.Geometry({MAIN: model, "3D/Objects/o.model": model})
        self.assertEqual(g.items, {"3d/3dmodel.model": [
            {"objectid": "1", "transform": "1 0 0 0 1 0 0 0 1 0 0 0", "printable": "0"}]})

    def test_objects_components_and_triangles(self):
        objs = [("1", fx.mesh_xml())] + [(str(i), '<components><component objectid="1"/></components>')
                                         for i in range(2, 12)]
        model = fx.model_xml(objs, [])
        with mock.patch.object(bc, "MAX_MODEL_OBJECTS", 10):
            with self.assertRaises(bc.GeometryError) as cm:
                bc.Geometry({MAIN: model})
            self.assertIn("more than 10 objects", str(cm.exception))
        with mock.patch.object(bc, "MAX_COMPONENTS", 9):
            with self.assertRaises(bc.GeometryError) as cm:
                bc.Geometry({MAIN: model})
            self.assertIn("more than 9 components", str(cm.exception))
        bc.Geometry({MAIN: model})
        tri = '<triangle v1="0" v2="1" v3="2"/>'
        mesh = fx.mesh_xml().replace("<triangles>" + tri, "<triangles>" + tri * 1001)
        with mock.patch.object(bc, "MAX_TRIANGLES", 1000):  # stands in for 20,000,001 triangles
            with self.assertRaises(bc.GeometryError) as cm:
                bc.Geometry({MAIN: fx.model_xml([("1", mesh)], [])}, triangles=True, max_triangles=10 ** 9)
            self.assertIn("more than 1000 triangles", str(cm.exception))
            bc.Geometry({MAIN: fx.model_xml([("1", mesh)], [])})  # triangles are not stored, so not counted


class ProblemLinesTest(BudgetBase):
    def test_repeated_problem_lines_are_capped(self):
        model = fx.model_xml([("1", fx.mesh_xml())], []).replace("<build>", "<build>" + "<item/>" * 5000)
        src = self.project("noid.3mf", model=model)
        r = self.verify(src)
        self.assertEqual(r.returncode, 1)
        lines = r.stdout.splitlines()
        self.assertEqual(lines.count("PROBLEM: a build item has no objectid"), 20)
        self.assertIn("PROBLEM: ... and 4980 more like this", lines)
        self.assertEqual(lines.index("PROBLEM: ... and 4980 more like this"),
                         lines.index("PROBLEM: a build item has no objectid") + 20)
        self.assertIn("5000 problem(s)", lines)
        self.assertLess(len(lines), 40)
        d = json.loads(self.verify(src, "--json").stdout)
        self.assertEqual(d["problems"].count("a build item has no objectid"), 20)
        self.assertIn("... and 4980 more like this", d["problems"])
        self.assertEqual(d["more"], {"a build item has no objectid": 4980})

    def test_lines_template_key(self):
        lines = verify_project.Lines()
        for i in range(25):
            lines.add("%s uses filament %d but the project has only %d", "Cube", i, 1)
        self.assertEqual(len(lines), 21)
        self.assertEqual(lines.more, {"_ uses filament _ but the project has only _": 5})
        self.assertEqual(lines.total(), 25)


class RefusalPathTest(BudgetBase):
    """L1 and L3: every refusal is a PROBLEM line with --json, never a traceback or a bare error."""

    def test_backslash_in_scan_and_gcode_comment_roles(self):
        for name in ("Metadata\\slice_info.config", "Metadata\\Slic3r_PE.config"):
            src = self.project("bs_%d.3mf" % len(name), extra=[(name, "; x = 1\n", False)])
            self.assertRefused(src, "has a backslash in its name")

    def test_not_a_zip_and_too_many_entries(self):
        p = self.path("text.3mf")
        with open(p, "w") as f:
            f.write("not a zip")
        self.assertRefused(p, "not a .3mf project (not a zip file)")
        many = self.raw("many.3mf", fx.project_entries(objects=ONE_OBJECT) +
                        [("Auxiliaries/f%05d" % i, b"", False) for i in range(bc.MAX_ENTRIES - 3)])
        self.assertRefused(many, "it has 10001 entries; the limit is 10000")

    def test_unexpected_error_inside_open_project(self):
        src = fx.write_3mf(self.path("ok.3mf"))
        with mock.patch.object(bc, "_index", side_effect=RuntimeError("boom")):
            code, out = self.verify_inprocess(src)
            self.assertEqual((code, out), (1, "PROBLEM: archive refused: internal error\n1 problem(s)\n"))
            code, out = self.verify_inprocess(src, "--json")
        self.assertEqual(code, 1)
        d = json.loads(out)
        self.assertEqual((d["refused"], d["problems"]), ("internal error", ["archive refused: internal error"]))


class ExactInflateTest(BudgetBase):
    """Z8 (L2): an entry is read only when its stream is exactly as long as the directory says."""

    def test_stream_longer_than_its_size(self):
        data = json.dumps(fx.project_settings()).encode()
        ents = base_entries()
        longer = data + b" " * 100 + json.dumps(fx.project_settings(post_process=[EVIL])).encode()
        ents[2] = fx.zip_entry(PS, longer, cd={"usize": len(data), "crc": zlib.crc32(data)})
        src = self.raw_bytes("longer.3mf", ents)
        with zipfile.ZipFile(src) as z:
            self.assertEqual(z.read(PS), data)  # CPython stops at the size and checks the CRC of what it read
        self.assertRefused(src, "inflates to more bytes than its size says")

    def test_stream_shorter_than_its_size(self):
        data = json.dumps(fx.project_settings()).encode()
        ents = base_entries()
        ents[2] = fx.zip_entry(PS, data, cd={"usize": len(data) + 10})
        self.assertRefused(self.raw_bytes("shorter.3mf", ents), "inflates to fewer bytes than its size says")

    def test_bytes_after_the_stream(self):
        data = json.dumps(fx.project_settings()).encode()
        ents = base_entries()
        ents[2] = fx.zip_entry(PS, data, comp=fx.deflate(data) + b"\0trailing")
        src = self.raw_bytes("trailing.3mf", ents)
        with zipfile.ZipFile(src) as z:
            self.assertEqual(z.read(PS), data)  # CPython ignores what follows the end of the stream
        self.assertRefused(src, "has bytes after its deflate stream")

    def test_stored_entry_one_byte_short(self):
        data = json.dumps(fx.project_settings()).encode()
        ents = base_entries()
        ents[2] = fx.zip_entry(PS, data, method=0, cd={"csize": len(data) + 1, "usize": len(data) + 1})
        self.assertRefused(self.raw_bytes("stored_short.3mf", ents), "fails its CRC check")

    def test_bad_stream_and_cut_short(self):
        data = json.dumps(fx.project_settings()).encode()
        ents = base_entries()
        ents[2] = fx.zip_entry(PS, data, comp=b"\xff" * 40)
        self.assertRefused(self.raw_bytes("badstream.3mf", ents), "is not a valid deflate stream")
        ents = base_entries()
        comp = fx.deflate(data)
        ents[2] = fx.zip_entry(PS, data, comp=comp[:-3])
        self.assertRefused(self.raw_bytes("cut.3mf", ents), "does not end within its compressed size")

    def test_normal_entries_read_exactly(self):
        for method in (0, 8):
            src = self.raw_bytes("ok%d.3mf" % method, base_entries(method=method))
            with bc.open_project(src) as idx:
                self.assertEqual(idx.findings, [])
                with zipfile.ZipFile(src) as z:
                    for info in idx.zip.infolist():
                        self.assertEqual(idx.read(info), z.read(info.filename))


def grid_mesh(n):
    """A wavy n x n vertex grid, 20 mm square and 3 mm high: n * n vertices, 2 (n - 1)^2 triangles."""
    step = 20.0 / (n - 1)
    vs = "".join('<vertex x="%.3f" y="%.3f" z="%.1f"/>' % (i * step, j * step, ((i + j) % 7) * 0.5)
                 for j in range(n) for i in range(n))
    ts = "".join('<triangle v1="%d" v2="%d" v3="%d"/><triangle v1="%d" v2="%d" v3="%d"/>' % (
        a, a + 1, a + n, a + 1, a + n + 1, a + n) for a in (j * n + i for j in range(n - 1) for i in range(n - 1)))
    return "<mesh><vertices>%s</vertices><triangles>%s</triangles></mesh>" % (vs, ts)


def large_model_settings(n_objects, n_plates):
    per = n_objects // n_plates
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for i in range(1, n_objects + 1):
        out += ['  <object id="%d">' % i, '    <metadata key="name" value="Part %d"/>' % i,
                '    <metadata key="extruder" value="1"/>', '    <part id="%d" subtype="normal_part">' % i,
                '      <metadata key="name" value="Part %d"/>' % i, "    </part>", "  </object>"]
    for p in range(1, n_plates + 1):
        out += ["  <plate>", '    <metadata key="plater_id" value="%d"/>' % p]
        out += ['    <model_instance>\n      <metadata key="object_id" value="%d"/>\n    </model_instance>' % i
                for i in range((p - 1) * per + 1, p * per + 1)]
        out.append("  </plate>")
    return "\n".join(out + ["</config>"]) + "\n"


class NormalLargeProjectTest(BudgetBase):
    """Guards the budget against over-refusal: the ADR's normal large project (50 objects, 10 plates, 20
    embedded presets, 2.6 M triangles) is read in full, in triangle mode too, well under every cap."""

    def test_normal_large_project_accepted(self):
        n_obj, n_plates, grid = 50, 10, 161
        mesh = grid_mesh(grid)
        per, cols = n_obj // n_plates, verify_project.plate_cols(n_plates)
        sx = sy = 256 * (1 + verify_project.PLATE_GAP)
        items = []
        for i in range(1, n_obj + 1):
            p, m = divmod(i - 1, per)
            ox, oy = (p % cols) * sx, -(p // cols) * sy
            items.append((str(i), "1 0 0 0 1 0 0 0 1 %.1f %.1f 0" % (ox + 10 + 45 * m, oy + 50)))
        model = fx.model_xml([(str(i), mesh) for i in range(1, n_obj + 1)], items)
        preset = dict(fx.project_settings(), **dict(("key_%d" % k, "value %d" % k) for k in range(300)))
        extra = [("Metadata/%s_settings_%d.config" % (kind, i), json.dumps(preset, indent=4), False)
                 for kind in ("process", "filament") for i in range(1, 11)]
        src = self.project("large.3mf", model=model, ms=large_model_settings(n_obj, n_plates), extra=extra)
        with bc.open_project(src) as idx:
            self.assertEqual((idx.findings, idx.more_findings, len(idx.presets)), ([], 0, 20))
            self.assertEqual(len(bc.parse_objects(idx.model_settings)), n_obj)
            self.assertEqual(len(bc.parse_plates(idx.model_settings)[1]), n_plates)
            geo = idx.geometry(triangles=True)
            placed = [geo.placed_meshes(MAIN, at["objectid"], bc.tf(at.get("transform")))
                      for at in geo.items["3d/3dmodel.model"]]
            used = dict(idx.budget.used)
        self.assertEqual(len(placed), n_obj)
        self.assertEqual(geo.stored_tris, n_obj * 2 * (grid - 1) ** 2)
        self.assertGreater(geo.stored_tris, 2500000)
        for kind, count in used.items():
            if kind != "model_elements":
                self.assertLess(count, bc.Budget().cap(kind) // 10, kind)
        # vertices, triangles and .model elements (mostly the two): 12-13% of their caps (the ADR said every
        # counter under 10%; see deviations 19 and 29)
        self.assertLess(used["model_elements"], bc.MAX_MODEL_ELEMENTS * 0.15)
        self.assertLess(geo.stored, bc.MAX_VERTICES * 0.15)
        self.assertLess(geo.stored_tris, bc.MAX_TRIANGLES * 0.15)
        r = self.verify(src, "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual((len(d["objects"]), d["plates"], d["problems"], d["more"]), (n_obj, n_plates, [], {}))


if __name__ == "__main__":
    unittest.main()
