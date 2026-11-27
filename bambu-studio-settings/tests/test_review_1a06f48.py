"""Regression tests for the security review at 1a06f48 (docs/reviews/skills-share-security-1a06f48.md): N1 (one
huge XML attribute in a small file made expat and Python build gigabytes before any cap was checked), problem
family untrusted-3mf/zip-resource-dos, and slice_check's number parsing ("inf" and "nan"). Every fixture is
generated here; none is committed.

Invariant (ADR 0006 D9): no byte-run taken from an untrusted file is copied or decoded into a new object unless
its size has been checked against a cap first."""
import json
import os
import re
import subprocess
import sys
import unittest
import zlib

import bss_fixtures as fx
import bambu_common as bc
import slice_check
from test_budget import BudgetBase, grid_mesh, large_model_settings
from test_untrusted_3mf import ONE_OBJECT

MB = 2 ** 20
CAP = bc.MAX_TAG_BYTES
# Markup up to CAP is always read; over CAP + XML_CHUNK it is always refused, wherever it falls against the pieces
# walk_xml feeds expat (ADR 0006, expat input invariant). In between it depends on where it starts.
OVER = CAP + bc.XML_CHUNK + 1
TOO_LONG = "markup longer than %d bytes" % CAP
MAIN = "3D/3dmodel.model"
MS = "Metadata/model_settings.config"
# One 512 MB entry held once, plus 256 MB: well under ADR 0006 D9's worst case (about 1.3 GB). Before the fix the
# review's file peaked at 3.1 GB (attribute) and 2.1 GB (object name).
PEAK_LIMIT_MB = 768
LETTERS = bytes(97 + b % 26 for b in range(256))

# Runs a script or open_project + geometry in a fresh process and writes that process's peak RSS (MB) to a file.
CHILD = r'''
import json, os, resource, runpy, sys
peak_file, what = sys.argv[1], sys.argv[2]
def peak():
    # VmHWM is this process's own peak. On Linux ru_maxrss also keeps the parent's peak from before exec (a test
    # run that had reached 1.1 GB made every child read 1.1 GB), so it is only the fallback.
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r // (1024 * 1024) if sys.platform == "darwin" else r // 1024
out = {}
try:
    if what == "geometry":
        sys.path.insert(0, sys.argv[3])
        import bambu_common as bc
        out["errors"] = []
        with bc.open_project(sys.argv[4]) as idx:
            for tri in (False, True):
                try:
                    idx.geometry(triangles=tri)
                    out["errors"].append(None)
                except bc.GeometryError as e:
                    out["errors"].append(str(e))
    else:
        sys.path.insert(0, os.path.dirname(what))
        sys.argv = [what] + sys.argv[3:]
        try:
            runpy.run_path(what, run_name="__main__")
        except SystemExit as e:
            out["exit"] = e.code
finally:
    out["peak_mb"] = peak()
    with open(peak_file, "w") as f:
        json.dump(out, f)
'''


def huge_value_entry(head, tail, size, random_bytes=4 * MB):
    """(deflate stream, crc, size) of head + random_bytes random letters + 'A's + tail, size bytes in all, built
    4 MB at a time so the test never holds the entry: the review's N1 shape (ratio about 165, under the 200 cap)."""
    c = zlib.compressobj(6, zlib.DEFLATED, -15)
    comp, crc, done = [], 0, 0
    fill = size - len(head) - len(tail) - random_bytes
    pieces = [head] + [os.urandom(MB).translate(LETTERS) for _ in range(random_bytes // MB)]
    piece = b"A" * (4 * MB)
    pieces += [piece] * (fill // len(piece)) + [b"A" * (fill % len(piece)), tail]
    for p in pieces:
        crc = zlib.crc32(p, crc)
        done += len(p)
        comp.append(c.compress(p))
    comp.append(c.flush())
    assert done == size
    return b"".join(comp), crc & 0xFFFFFFFF, size


def run_child(tmp, what, *args):
    peak_file = os.path.join(tmp, "peak.json")
    p = subprocess.run([sys.executable, "-I", "-c", CHILD, peak_file, what] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=600)
    with open(peak_file) as f:
        out = json.load(f)
    return out, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


@unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin", "peak RSS needs resource")
class HugeAttributeTest(BudgetBase):
    """N1 as the review built it: a ~3 MB .3mf whose 512 MB 3D/3dmodel.model is one <vertex x="..."> (or one
    object name=) of 512 MB. Refused before expat builds the value, by verify, geometry() and
    geometry(triangles=True), with the peak under D9's worst case."""

    def attack(self, name, where):
        main = fx.model_xml([("1", fx.mesh_xml())], [("1", "1 0 0 0 1 0 0 0 1 50 50 0")]).encode()
        if where == "vertex":
            k = main.index(b'<vertex x="') + len(b'<vertex x="')
            j = main.index(b'"', k)
        else:
            k = main.index(b'<object id="1"') + len(b'<object id="1"')
            main = main[:k] + b' name=""' + main[k:]
            k = j = k + len(b' name="')
        comp, crc, size = huge_value_entry(main[:k], main[j:], bc.MAX_ENTRY_BYTES - 4096)
        ents = [fx.zip_entry(n, b"", comp=comp, cd={"usize": size, "crc": crc}) if n == MAIN else fx.zip_entry(n, d)
                for n, d, _ in fx.project_entries(objects=ONE_OBJECT, model=main.decode())]
        src = self.raw_bytes(name, ents)
        self.assertLess(os.path.getsize(src), 4 * MB)
        return src

    def assertGeometryRefused(self, src):
        out, _, err = run_child(self.tmp, "geometry", fx.SCRIPTS, src)
        self.assertEqual(len(out.get("errors", [])), 2, err)
        for e in out["errors"]:
            self.assertIn(TOO_LONG, e or "")
        self.assertLess(out["peak_mb"], PEAK_LIMIT_MB)

    def test_huge_vertex_attribute(self):
        src = self.attack("vertex.3mf", "vertex")
        self.assertGeometryRefused(src)
        out, stdout, err = run_child(self.tmp, os.path.join(fx.SCRIPTS, "verify_project.py"), src,
                                     "--profiles", self.prof)
        self.assertIn("geometry not checked", stdout)
        self.assertIn(TOO_LONG, stdout)
        self.assertNotIn("Traceback", stdout + err)
        self.assertLess(out["peak_mb"], PEAK_LIMIT_MB)

    def test_huge_object_name(self):
        self.assertGeometryRefused(self.attack("name.3mf", "name"))


class TagCapTest(BudgetBase):
    """N1 at the XML reader, as rebuilt after the b81ad41 re-check: walk_xml feeds expat XML_CHUNK at a time and
    refuses once expat holds more than MAX_TAG_BYTES it has not turned into an event, whatever the token. Every
    consumer of walk_xml (xml_tree for settings, metadata and scan entries, tune and slice_check's trees,
    Geometry.add for every .model) goes through it."""

    def refused(self, data, why=TOO_LONG):
        with self.assertRaises(bc.BadEntry) as cm:
            bc.xml_tree(data, "x")
        self.assertIn(why, str(cm.exception))

    def tag(self, size, value_char=b"v"):
        """<a v="..."/> of exactly size bytes."""
        return b'<a v="' + value_char * (size - 9) + b'"/>'

    def test_tag_at_and_over_the_cap(self):
        self.assertEqual(len(self.tag(CAP)), CAP)
        for pad in (0, 1, CAP // 2, bc.XML_CHUNK - 4, bc.XML_CHUNK - 3, bc.XML_CHUNK):  # against the 64 KB pieces
            head = b"<r>" + b" " * pad
            tree = bc.xml_tree(head + self.tag(CAP) + b"</r>", "x")
            self.assertEqual(len(tree.children[0].children[0].attrs["v"]), CAP - 9)
            self.refused(head + self.tag(OVER) + b"</r>")
        self.refused(self.tag(OVER))  # the root element itself
        self.refused(b"<r>" + self.tag(OVER + 4)[:-3])  # never closed
        self.refused(b"<r></r" + b" " * OVER + b">")  # an end tag

    def test_value_holding_gt_signs(self):
        """'>' is allowed in an attribute value; expat, not a byte scan, finds where the tag ends."""
        value = (b">" + b"v" * 999) * 140
        self.refused(b'<r><a v="' + value + b'"/></r>')
        tree = bc.xml_tree(b'<r><a v="' + value[:CAP - 20] + b'"/></r>', "x")
        self.assertEqual(tree.children[0].children[0].attrs["v"], value[:CAP - 20].decode())

    def test_text_comments_processing_instructions_cdata(self):
        long_text = b"t" * (4 * OVER)
        tree = bc.xml_tree(b"<r><a/>" + long_text + b"<b/></r>", "x")  # text streams through expat
        self.assertEqual(tree.children[0].text, long_text.decode())
        # a raw '>' in text is legal XML and now read (the old byte scan could not tell it from a long tag)
        tree = bc.xml_tree(b"<r><a/>" + long_text + b"></r>", "x")
        self.assertEqual(tree.children[0].text, long_text.decode() + ">")
        inner = (b"x" * 1000 + b"<") * 140  # a '<' every 1000 bytes changes nothing: the comment is one token
        self.refused(b"<r><!--" + inner + b"--></r>")
        self.refused(b"<r><?pi " + inner + b"?></r>")
        self.refused(b"<?xml version='1.0'" + b" " * OVER + b"?><r/>")
        bc.xml_tree(b"<r><!--" + inner[:CAP - 10] + b"--><?pi " + inner[:CAP - 10] + b"?></r>", "x")
        cdata = (b"x" * 1000 + b"<a>") * 300  # CDATA streams: any size, and its '<' starts no tag
        tree = bc.xml_tree(b"<r><![CDATA[" + cdata + b"]]></r>", "x")
        self.assertEqual(tree.children[0].text, cdata.decode())
        bc.xml_tree(b"<r><!-- <!DOCTYPE r> --></r>", "x")  # a DOCTYPE inside a comment is text
        self.refused(b"<!DOCTYPE r><r/>", "DOCTYPE")
        self.refused(b'<!DOCTYPE r SYSTEM "' + b"s" * OVER + b'"><r/>')  # the literal is held: refused first

    def test_references_and_prolog_junk(self):
        """The b81ad41 re-check's shapes, small: each is one token expat holds whole until it ends."""
        for data in (b"<r>&" + b"a" * OVER + b";</r>", b"<r>&#" + b"0" * OVER + b"65;</r>",
                     b"<r>&#x" + b"0" * OVER + b"41;</r>", b'<r v="&' + b"a" * OVER + b';"/>',
                     b"a" * OVER + b"<r/>", b"%" + b"a" * OVER + b"<r/>", b"1" * OVER + b"<r/>",
                     b"<r/><!--" + b"c" * OVER):
            self.refused(data)
        tree = bc.xml_tree(b"<r>&#" + b"0" * 1000 + b"65;&amp;</r>", "x")  # short ones are read as before
        self.assertEqual(tree.children[0].text, "A&")

    def test_markup_is_not_counted(self):
        """Comments and processing instructions run in expat's C code with no Python handler, so their number is
        no longer capped (MAX_MARKUP went with the byte scan)."""
        bc.xml_tree(b"<r>" + b"<!---->" * 50000 + b"<?a?>" * 50000 + b"<![CDATA[]]>" * 50000 + b"</r>", "x")

    def test_across_chunk_boundaries(self):
        """walk_xml feeds expat XML_CHUNK at a time, pyexpat splits anything over 1 MB, and the attribute scan
        steps 4 MB: a tag that starts just before any of these boundaries is read with the right byte positions,
        and one over the cap is refused."""
        for boundary in (bc.XML_CHUNK, MB, 4 * MB):
            head = b"<r>" + b" " * (boundary - 3 - 5)
            data = head + b'<a v="1"/><b>' + b"</b></r>"
            seen = []
            bc.walk_xml(data, "x", lambda t, a, i: seen.append((t, i)), lambda t, i: seen.append((t, i)))
            k = len(head)
            self.assertEqual(seen, [("r", 0), ("a", k), ("a", k + 10), ("b", k + 10), ("b", k + 17),
                                    ("r", len(data))])
            self.refused(b"<r>" + b" " * (boundary - 3 - CAP // 2) + self.tag(OVER) + b"</r>")

    def test_settings_entry_refused(self):
        ms = fx.model_settings(ONE_OBJECT).replace('value="Cube A"', 'value="%s"' % ("v" * OVER), 1)
        reason = self.refused_reason(self.project("long.3mf", ms=ms))
        self.assertIn(TOO_LONG, reason)

    def test_normal_large_project_tags(self):
        """The normal large project's tags (ADR D9; deviation 39 measured the full one): the cap is over 500 times
        the longest real tag."""
        model = fx.model_xml([(str(i), grid_mesh(11)) for i in range(1, 51)],
                             [(str(i), "1 0 0 0 1 0 0 0 1 %.1f %.1f 0" % (1000.5 + i, -1000.5)) for i in range(1, 51)])
        for data in (model, large_model_settings(50, 10)):
            longest = max(len(t) for t in re.findall(r"<[^>]*>", data))
            self.assertLess(longest * 500, CAP)


class SliceInfoNumbersTest(unittest.TestCase):
    """slice_check read "inf" in a slice_info prediction as a number, and int() of it raised OverflowError."""

    def test_inf_and_nan_are_not_numbers(self):
        for s in ("inf", "-inf", "nan", "1e400", "Infinity"):
            self.assertIsNone(slice_check._num(s), s)
            self.assertEqual(int(slice_check._num(s) or 0), 0)
        self.assertEqual(slice_check._num("600"), 600.0)
        self.assertIsNone(slice_check._num("x"))
        stats = slice_check.gcode_stats("; Z_HEIGHT: inf\n; LAYER_HEIGHT: nan\n; FEATURE: Wall\nG1 X1 E1e999\n")
        self.assertIsInstance(stats, dict)


if __name__ == "__main__":
    unittest.main()
