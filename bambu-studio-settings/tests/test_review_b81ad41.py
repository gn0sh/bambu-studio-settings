"""Regression tests for the security re-check at b81ad41 (docs/reviews/skills-share-security-b81ad41.md), problem
family untrusted-3mf/zip-resource-dos, seventh hit: expat held whole tokens that the byte scan before it did not
look at (an entity or character reference in text, junk before the root), so a 3 MB file still cost 3 GB
(huge-entity-ref); the scan itself was slow on runs of '<' (lt-scan-cpu). Also expat's kept names (ADR 0006
deviation 48), found while listing what expat keeps. Every fixture is generated here; none is committed.

Invariant (ADR 0006, expat input invariant): expat never holds more than MAX_TAG_BYTES + XML_CHUNK of input it has
not yet turned into an event, and keeps at most MAX_XML_NAME_CHARS characters of distinct names; the reader
enforces both itself, independent of token type."""
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

import bss_fixtures as fx
import bambu_common as bc
from test_budget import BudgetBase, grid_mesh, large_model_settings
from test_review_1a06f48 import MAIN, MB, PEAK_LIMIT_MB, TOO_LONG, huge_value_entry, run_child
from test_untrusted_3mf import ONE_OBJECT

NAMES = "characters of distinct element and attribute names"
SIZE = 200 * MB  # the re-check's in-memory entry size
# What walk_xml may add to the entry it is given: expat's buffer (at most 128 KB), one tag's strings, the 4 MB
# steps of the attribute scan. Before the fix each shape below added 260-420 MB to a 200 MB entry.
GROWTH_LIMIT_MB = 32
PEAK_RSS = sys.platform.startswith("linux") or sys.platform == "darwin"

# Reads one file whole, then walk_xml on it, in a fresh process; prints the peak RSS (MB) before and after.
WALK_CHILD = r'''
import json, resource, sys
sys.path.insert(0, sys.argv[1])
import bambu_common as bc
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
with open(sys.argv[2], "rb") as f:
    data = f.read()
out = {"base_mb": peak()}
try:
    bc.walk_xml(data, "x", lambda *a: None, None, lambda s: None)
    out["result"] = "parsed"
except bc.BadEntry as e:
    out["result"] = str(e)
out["peak_mb"] = peak()
print(json.dumps(out))
'''


def write_file(path, head, unit, size, tail=b""):
    """head + whole units + tail, about size bytes, written 4 MB at a time (the test never holds it)."""
    units = (size - len(head) - len(tail)) // len(unit)
    per = 4 * MB // len(unit)
    with open(path, "wb") as f:
        f.write(head)
        for _ in range(units // per):
            f.write(unit * per)
        f.write(unit * (units % per) + tail)
    return path


def walk_in_child(path):
    p = subprocess.run([sys.executable, "-I", "-c", WALK_CHILD, fx.SCRIPTS, path], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=600)
    err = p.stderr.decode("utf-8", "replace")
    if p.returncode != 0:
        raise AssertionError(err)
    return json.loads(p.stdout.decode()), err


@unittest.skipUnless(PEAK_RSS, "peak RSS needs resource")
class EntityReferenceFileTest(BudgetBase):
    """The re-check's file: a 3 MB .3mf whose 512 MB 3D/3dmodel.model holds <metadata name="Title">&, about 508 MB
    of name characters, then ;</metadata>. Before: open_project + geometry() twice peaked at 3,123 MB and verify at
    1,577 MB, both refused only after the memory was spent."""

    def test_huge_entity_reference_in_model_text(self):
        main = fx.model_xml([("1", fx.mesh_xml())], [("1", "1 0 0 0 1 0 0 0 1 50 50 0")]).encode()
        k = main.index(b"<resources>")
        head, tail = main[:k] + b'<metadata name="Title">&', b";</metadata>" + main[k:]
        comp, crc, size = huge_value_entry(head, tail, bc.MAX_ENTRY_BYTES - 4096)
        ents = [fx.zip_entry(n, b"", comp=comp, cd={"usize": size, "crc": crc}) if n == MAIN else fx.zip_entry(n, d)
                for n, d, _ in fx.project_entries(objects=ONE_OBJECT, model=main.decode())]
        src = self.raw_bytes("entity.3mf", ents)
        self.assertLess(os.path.getsize(src), 4 * MB)
        out, _, err = run_child(self.tmp, "geometry", fx.SCRIPTS, src)
        self.assertLess(out["peak_mb"], PEAK_LIMIT_MB)
        self.assertEqual(len(out.get("errors", [])), 2, err)
        for e in out["errors"]:
            self.assertIn(TOO_LONG, e or "")
        out, stdout, err = run_child(self.tmp, os.path.join(fx.SCRIPTS, "verify_project.py"), src,
                                     "--profiles", self.prof)
        self.assertLess(out["peak_mb"], PEAK_LIMIT_MB)
        self.assertIn("geometry not checked", stdout)
        self.assertIn(TOO_LONG, stdout)
        self.assertNotIn("Traceback", stdout + err)


@unittest.skipUnless(PEAK_RSS, "peak RSS needs resource")
class HeldInputPeakTest(fx.TempDirTest):
    """The re-check's in-memory shapes, 200 MB each, through walk_xml in a fresh process: each is one token expat
    would hold whole. Refused with what walk_xml adds to the entry under GROWTH_LIMIT_MB (before: +260-420 MB)."""

    SHAPES = {
        "entity reference in text": (b"<a>&", b"a", b";</a>"),
        "decimal character reference": (b"<a>&#", b"0", b"65;</a>"),
        "hex character reference": (b"<a>&#x", b"0", b"41;</a>"),
        "name characters before the root": (b"", b"a", b"<a/>"),
        "parameter reference before the root": (b"%", b"a", b"<a/>"),
        "digits before the root": (b"", b"1", b"<a/>"),
        "entity reference in an attribute": (b'<a b="&', b"a", b';"/>'),
        "comment after the root": (b"<a/><!--", b"c", b""),
    }
    # Same size, streamed by expat: read, with the same small growth (no over-refusal, nothing held).
    CONTROLS = {
        "text": (b"<a>", b"x", b"</a>"),
        "CDATA": (b"<a><![CDATA[", b"x", b"]]></a>"),
        "character references": (b"<a>", b"&#65;", b"</a>"),
        "whitespace before the root": (b'<?xml version="1.0"?>', b" ", b"<a/>"),
    }

    def check(self, shapes, want):
        for name, (head, unit, tail) in shapes.items():
            with self.subTest(name):
                path = write_file(self.path("entry.xml"), head, unit, SIZE, tail)
                try:
                    out, err = walk_in_child(path)
                finally:
                    os.remove(path)
                self.assertLess(out["peak_mb"] - out["base_mb"], GROWTH_LIMIT_MB, out)
                self.assertLess(out["peak_mb"], PEAK_LIMIT_MB, out)
                self.assertIn(want, out["result"], name)

    def test_held_tokens_refused(self):
        self.check(self.SHAPES, TOO_LONG)

    def test_streamed_input_read(self):
        self.check(self.CONTROLS, "parsed")


class NamesTest(BudgetBase):
    """expat keeps every distinct element and attribute name for the whole parse (its DTD tables) and pyexpat
    keeps each as a str (its intern dict): 100 MB of distinct attribute names added 1.1 GB (deviation 48).
    walk_xml counts the characters of each new name against MAX_XML_NAME_CHARS."""

    def names(self, data):
        seen = set()

        def start(tag, attrs, i):
            seen.add(tag)
            seen.update(attrs)
        bc.walk_xml(data, "x", start)
        return sum(map(len, seen))

    def test_counted_once_at_the_cap(self):
        data = b'<r a="" b=""><r a=""/><s b="" r=""/>' + b'<s a=""/>' * 1000 + b"</r>"
        self.assertEqual(self.names(data), 4)  # r, a, b, s: repeats and a name used for both count once
        with mock.patch.object(bc, "MAX_XML_NAME_CHARS", 4):
            bc.xml_tree(data, "x")
        with mock.patch.object(bc, "MAX_XML_NAME_CHARS", 3), self.assertRaises(bc.BadEntry) as cm:
            bc.xml_tree(data, "x")
        self.assertIn("more than 3 %s" % NAMES, str(cm.exception))

    def test_distinct_attribute_and_element_names(self):
        n = bc.MAX_XML_NAME_CHARS // 10 + 1000  # names of 10 characters, a little over the cap together
        attrs = b"<r>" + b"".join(b"<v" + b"".join(b' n%09d=""' % (i + k) for k in range(500)) + b"/>"
                                  for i in range(0, n, 500)) + b"</r>"
        elems = b"<r>" + b"".join(b"<e%09d/>" % i for i in range(n)) + b"</r>"
        for data in (attrs, elems):
            with self.assertRaises(bc.BadEntry) as cm:
                bc.xml_tree(data, "x")
            self.assertIn(NAMES, str(cm.exception))

    def test_model_file_refused_in_geometry(self):
        """The .model path (Geometry.add, for verify and the analyzer) is where nothing else caps it: unknown
        elements only count toward MAX_MODEL_OTHER."""
        main = fx.model_xml([("1", fx.mesh_xml())], [("1", "1 0 0 0 1 0 0 0 1 50 50 0")])
        k = main.index("<resources>")
        junk = "".join("<x%s/>" % "".join(' n%09d=""' % (i + j) for j in range(500))
                       for i in range(0, bc.MAX_XML_NAME_CHARS // 10 + 1000, 500))
        src = self.project("names.3mf", model=main[:k] + junk + main[k:])
        with bc.open_project(src) as idx, self.assertRaises(bc.GeometryError) as cm:
            idx.geometry()
        self.assertIn(NAMES, str(cm.exception))

    def test_normal_large_project_names(self):
        """Real files use a few dozen names: the cap is over 500 times the normal large project's total."""
        model = fx.model_xml([(str(i), grid_mesh(11)) for i in range(1, 51)],
                             [(str(i), "1 0 0 0 1 0 0 0 1 %.1f %.1f 0" % (1000.5 + i, -1000.5)) for i in range(1, 51)])
        for data in (model, large_model_settings(50, 10)):
            self.assertLess(self.names(data.encode()) * 500, bc.MAX_XML_NAME_CHARS)


@unittest.skipUnless(PEAK_RSS, "peak RSS needs resource")
class DistinctNamesPeakTest(fx.TempDirTest):
    """The two name shapes at size, in a fresh process. Before the fix 64 MB of distinct attribute names added
    about 700 MB and 30 MB of distinct element names about 520 MB; both are refused now with little growth."""

    def test_distinct_names_refused_flat(self):
        for name, size, unit in (("attributes", 64 * MB, lambda i: b"<v" + b"".join(
                b' n%09d=""' % (i * 1000 + k) for k in range(1000)) + b"/>"),
                                 ("elements", 30 * MB, lambda i: b"".join(
                                     b"<e%09d/>" % (i * 1000 + k) for k in range(1000)))):
            with self.subTest(name):
                path = self.path("names.xml")
                with open(path, "wb") as f:
                    f.write(b"<r>")
                    done, i = 3, 0
                    while done < size:
                        piece = unit(i)
                        f.write(piece)
                        done, i = done + len(piece), i + 1
                    f.write(b"</r>")
                try:
                    out, err = walk_in_child(path)
                finally:
                    os.remove(path)
                self.assertLess(out["peak_mb"] - out["base_mb"], GROWTH_LIMIT_MB, out)
                self.assertIn(NAMES, out["result"])


class LtScanTest(unittest.TestCase):
    """lt-scan-cpu: 200 MB of '<' took 11.3 s in the old byte scan before expat refused it at byte 2. walk_xml no
    longer scans for tags, so a run of '<' costs no more than plain text of the same size (timed against it,
    so the bound does not depend on the machine)."""

    def timed(self, data):
        t = time.perf_counter()
        try:
            bc.walk_xml(data, "x", lambda *a: None)
        except bc.BadEntry:
            pass
        return time.perf_counter() - t

    def test_runs_of_lt_are_cheap(self):
        n = 64 * MB
        text = self.timed(b"<a>" + b"x" * n + b"</a>")
        for data in (b"<" * n, b"<!-" * (n // 3), b"<a>" + b"<" * n):
            self.assertLess(self.timed(data), 2 * text + 0.5)


if __name__ == "__main__":
    unittest.main()
