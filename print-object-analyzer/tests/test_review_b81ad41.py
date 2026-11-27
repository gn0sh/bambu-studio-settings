"""Regression tests for the security re-check at b81ad41 (docs/reviews/skills-share-security-b81ad41.md) in the
analyzer, problem family untrusted-3mf/zip-resource-dos, seventh hit: a 3 MB .3mf whose .model holds one huge
entity reference in text made bambu_common's reader hold the whole 508 MB token (huge-entity-ref), and distinct
element and attribute names are kept by expat for the whole parse (ADR 0006 deviation 48). The analyzer reads
every .model through bambu_common's Geometry, so walk_xml's held-input bound and names cap cover it; these tests
hold it to that. Every fixture is generated here; none is committed."""
import json
import os
import subprocess
import sys
import unittest
import zipfile

import poa_fixtures as fx
import mesh_io
import poa_common as pc
from test_review_1a06f48 import CHILD, LETTERS, MB, PEAK_LIMIT_MB

sys.path.insert(0, fx.BSS_SCRIPTS)
import bambu_common as bc  # noqa: E402

TOO_LONG = "markup longer than %d bytes" % bc.MAX_TAG_BYTES
OVER = bc.MAX_TAG_BYTES + bc.XML_CHUNK + 1
NAMES = "characters of distinct element and attribute names"


def with_model_insert(src, dst, head_insert, tail_insert, size=None, random_bytes=4 * MB, fill=b""):
    """dst: src with head_insert + filler + tail_insert put before <resources> in its first .model. With size, the
    filler is random_bytes random letters, then 'A's, to make the .model size bytes in all (ratio about 165, under
    the 200 cap), written 4 MB at a time so the test never holds the entry; without, it is fill."""
    done = False
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if done or not info.filename.endswith(".model") or b"<resources>" not in data:
                zout.writestr(info.filename, data)
                continue
            k = data.index(b"<resources>")
            head, tail = data[:k] + head_insert, tail_insert + data[k:]
            with zout.open(info.filename, "w") as w:
                w.write(head)
                if size is None:
                    w.write(fill)
                else:
                    rest = size - len(head) - len(tail) - random_bytes
                    for _ in range(random_bytes // MB):
                        w.write(os.urandom(MB).translate(LETTERS))
                    piece = b"A" * (4 * MB)
                    for _ in range(rest // len(piece)):
                        w.write(piece)
                    w.write(b"A" * (rest % len(piece)))
                w.write(tail)
            done = True
    assert done
    return dst


class HeldInputTest(fx.TempDir):
    def project(self):
        return fx.write_project(self.path("small.3mf"), [{"name": "A", "mesh": fx.box(0, 0, 0, 10, 10, 5)}])

    def load_error(self, src):
        with self.assertRaises(pc.UserError) as cm:
            mesh_io.load_inputs([src])
        return str(cm.exception)

    def test_entity_reference_is_a_clean_error(self):
        src = with_model_insert(self.project(), self.path("ref.3mf"), b'<metadata name="Title">&', b";</metadata>",
                                fill=b"a" * OVER)
        self.assertIn(TOO_LONG, self.load_error(src))

    def test_distinct_names_are_a_clean_error(self):
        junk = b"".join(b"<x" + b"".join(b' n%09d=""' % (i + j) for j in range(500)) + b"/>"
                        for i in range(0, bc.MAX_XML_NAME_CHARS // 10 + 1000, 500))
        src = with_model_insert(self.project(), self.path("names.3mf"), b"", b"", fill=junk)
        self.assertIn(NAMES, self.load_error(src))

    @unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin", "peak RSS needs resource")
    def test_review_file_refused_under_the_peak(self):
        src = with_model_insert(self.project(), self.path("entity.3mf"), b'<metadata name="Title">&',
                                b";</metadata>", size=bc.MAX_ENTRY_BYTES - 4096)
        self.assertLess(os.path.getsize(src), 4 * MB)
        peak_file = self.path("peak.json")
        script = os.path.join(fx.SCRIPTS, "analyze_objects.py")
        p = subprocess.run([sys.executable, "-I", "-c", CHILD, peak_file, script, src], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=600)
        err = p.stderr.decode("utf-8", "replace")
        with open(peak_file) as f:
            out = json.load(f)
        self.assertLess(out["peak_mb"], PEAK_LIMIT_MB)
        msg = str(out.get("exit"))  # run_main exits with "error: ..." (printed to stderr outside this wrapper)
        self.assertTrue(msg.startswith("error: "), msg + err)
        self.assertIn("geometry not read", msg)
        self.assertIn(TOO_LONG, msg)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
