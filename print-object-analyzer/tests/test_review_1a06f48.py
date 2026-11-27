"""Regression tests for the security review at 1a06f48 (docs/reviews/skills-share-security-1a06f48.md), N1 in the
analyzer: one huge XML attribute in a small .3mf made bambu_common's reader build gigabytes before any cap was
checked (problem family untrusted-3mf/zip-resource-dos). The analyzer reads every .model through bambu_common's
Geometry, so the reader's held-input bound (MAX_TAG_BYTES) covers it; these tests hold it to that. Every fixture is
generated here; none is committed."""
import json
import os
import subprocess
import sys
import unittest
import zipfile

import poa_fixtures as fx
import mesh_io
import poa_common as pc

sys.path.insert(0, fx.BSS_SCRIPTS)
import bambu_common as bc  # noqa: E402

MB = 2 ** 20
CAP = bc.MAX_TAG_BYTES
# One 512 MB entry held once, plus 256 MB: well under ADR 0006 D9's worst case (about 1.3 GB). Before the fix the
# review's file peaked at 3.1 GB.
PEAK_LIMIT_MB = 768
LETTERS = bytes(97 + b % 26 for b in range(256))

# Runs analyze_objects.py in a fresh process; writes its exit code or message and peak RSS (MB) to a file.
CHILD = r'''
import json, os, resource, runpy, sys
peak_file, script = sys.argv[1], sys.argv[2]
out = {}
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
try:
    sys.path.insert(0, os.path.dirname(script))
    sys.argv = [script] + sys.argv[3:]
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        out["exit"] = e.code
finally:
    out["peak_mb"] = peak()
    with open(peak_file, "w") as f:
        json.dump(out, f)
'''


def with_huge_vertex(src, dst, size=bc.MAX_ENTRY_BYTES - 4096, random_bytes=4 * MB):
    """dst: src with the first vertex x value of its first .model with vertices grown to size bytes in all
    (random letters, then 'A's: ratio about 165, under the 200 cap), written 4 MB at a time so the test never holds
    the entry."""
    done = False
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if done or not info.filename.endswith(".model") or b'<vertex x="' not in data:
                zout.writestr(info.filename, data)
                continue
            k = data.index(b'<vertex x="') + len(b'<vertex x="')
            head, tail = data[:k], data[data.index(b'"', k):]
            fill = size - len(head) - len(tail) - random_bytes
            with zout.open(info.filename, "w") as w:
                w.write(head)
                for _ in range(random_bytes // MB):
                    w.write(os.urandom(MB).translate(LETTERS))
                piece = b"A" * (4 * MB)
                for _ in range(fill // len(piece)):
                    w.write(piece)
                w.write(b"A" * (fill % len(piece)) + tail)
            done = True
    assert done
    return dst


class HugeAttributeTest(fx.TempDir):
    def project(self):
        return fx.write_project(self.path("small.3mf"), [{"name": "A", "mesh": fx.box(0, 0, 0, 10, 10, 5)}])

    def test_tag_over_the_cap_is_a_clean_error(self):
        small = with_huge_vertex(self.project(), self.path("long.3mf"), size=4 * CAP, random_bytes=0)
        with self.assertRaises(pc.UserError) as cm:
            mesh_io.load_inputs([small])
        self.assertIn("markup longer than %d bytes" % CAP, str(cm.exception))

    @unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin", "peak RSS needs resource")
    def test_review_file_refused_under_the_peak(self):
        src = with_huge_vertex(self.project(), self.path("vertex.3mf"))
        self.assertLess(os.path.getsize(src), 4 * MB)
        peak_file = self.path("peak.json")
        script = os.path.join(fx.SCRIPTS, "analyze_objects.py")
        p = subprocess.run([sys.executable, "-I", "-c", CHILD, peak_file, script, src], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=600)
        err = p.stderr.decode("utf-8", "replace")
        with open(peak_file) as f:
            out = json.load(f)
        msg = str(out.get("exit"))  # run_main exits with "error: ..." (printed to stderr outside this wrapper)
        self.assertTrue(msg.startswith("error: "), msg + err)
        self.assertIn("geometry not read", msg)
        self.assertIn("markup longer than %d bytes" % CAP, msg)
        self.assertNotIn("Traceback", err)
        self.assertLess(out["peak_mb"], PEAK_LIMIT_MB)


if __name__ == "__main__":
    unittest.main()
