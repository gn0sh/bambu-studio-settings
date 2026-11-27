"""registry.py bounds every length before any parse: one cap on any string in the registry or on the command line,
per-field limits in validate, and a digit cap on JSON numbers. A 1 MB string in any field is refused cleanly and
fast. Runs registry.main() in-process (a 1 MB command-line argument is over Linux's per-argument limit)."""
import contextlib
import copy
import io
import json
import sys
import time

import ptt_fixtures as fx
import registry

MB = fx.MB
BIG = "A" * MB


def leaves(v, path=()):
    """Every (path, is_key) in a JSON value: each value and each object key."""
    if isinstance(v, dict):
        for k, x in v.items():
            yield path + (k,), True
            for p in leaves(x, path + (k,)):
                yield p
    elif isinstance(v, list):
        for i, x in enumerate(v):
            for p in leaves(x, path + (i,)):
                yield p
    else:
        yield path, False


def replaced(reg, path, is_key, new):
    reg = copy.deepcopy(reg)
    parent = reg
    for k in path[:-1]:
        parent = parent[k]
    if is_key:
        parent[new] = parent.pop(path[-1])
    else:
        parent[path[-1]] = new
    return reg


class Main(fx.TempDir):
    def main(self, *args):
        """(exit code, stdout, stderr, seconds) of registry.main() with these arguments."""
        out, err = io.StringIO(), io.StringIO()
        old = sys.argv
        sys.argv = ["registry.py", "--registry", self.path("filaments.json")] + list(args)
        t = time.perf_counter()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    registry.main(); code = 0
                except SystemExit as e:
                    code = e.code
        finally:
            sys.argv = old
        dt = time.perf_counter() - t
        if isinstance(code, str):
            err.write(code); code = 1
        return code or 0, out.getvalue(), err.getvalue(), dt


class OversizedTestId(Main):
    def test_huge_id_refused(self):
        for digits, why in ((5000000, "over 2 MB"), (MB, "characters (max 64)")):
            reg = fx.registry(); reg["filaments"][0]["tests"][0]["id"] = "T-20261127-P1S-" + "9" * digits
            self.write_registry(reg)
            for args in (("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x", "--dry-run"), ("validate",)):
                with self.subTest((digits, args[0])):
                    code, out, err, dt = self.main(*args)
                    self.assertNotEqual(code, 0)
                    self.assertIn(why, err)
                    self.assertLess(dt, 1.0)

    def test_bad_id_refused_at_load(self):
        """An id that is not T-YYYYMMDD-PRINTER-NN (here 5 digits) refuses the file; nothing numbers past it."""
        reg = fx.registry(); reg["filaments"][0]["tests"][0]["id"] = "T-20261127-P1S-12345"
        self.write_registry(reg)
        for args in (("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x", "--date", "2026-11-27",
                      "--dry-run"), ("validate",)):
            code, out, err, dt = self.main(*args)
            self.assertEqual(code, 1)
            self.assertEqual(out, "")
            self.assertIn("filaments[0].tests[0].id: 'T-20261127-P1S-12345' is not a test id T-YYYYMMDD-PRINTER-NN", err)

    def test_field_limits(self):
        reg = fx.registry(); reg["filaments"][0]["tests"][0]["id"] = "T-20261127-P1S-" + "0" * 300
        self.write_registry(reg)
        code, out, err, dt = self.main("validate")
        self.assertEqual(code, 1)
        self.assertIn("filaments[0].tests[0].id: a string of 315 characters (max 64)", err)


class Sweep(Main):
    COMMANDS = (("validate",), ("list",), ("show", "PLA-NAVY-01"), ("spec", "PLA-NAVY-01", "--printer", "P1S"),
                ("record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x", "--dry-run"))

    def base(self):
        reg = fx.registry(filament={"nozzle_temperature": fx.setting("215")}, process={"seam_gap": fx.setting("10%")})
        reg["notes"] = ["a note"]
        reg["filaments"][0]["presets"] = {"P1S": "Bambu PLA Basic @BBL P1S 0.4 nozzle"}
        return reg

    def check(self, label):
        for args in self.COMMANDS:
            code, out, err, dt = self.main(*args)
            self.assertNotEqual(code, 0, "%s: %s accepted" % (label, args[0]))
            self.assertNotIn("Traceback", err)
            self.assertLess(dt, 1.0, "%s: %s took %.2f s" % (label, args[0], dt))

    def test_one_megabyte_in_every_field(self):
        reg = self.base()
        paths = list(leaves(reg))
        self.assertGreater(len(paths), 50)
        for path, is_key in paths:
            label = "%s%s" % ("key " if is_key else "", " > ".join(map(str, path)))
            with self.subTest(label):
                self.write_registry(replaced(reg, path, is_key, BIG))
                self.check(label)

    def test_one_megabyte_number(self):
        for field in ("diameter_mm", "hex_is_estimate"):
            with self.subTest(field):
                text = json.dumps(self.base()).replace('"%s": ' % field, '"%s": %s, "x": ' % (field, "9" * MB), 1)
                self.write("filaments.json", text)
                self.check(field)
                code, out, err, dt = self.main("validate")
                self.assertIn("digits (max %d)" % registry.MAX_NUMBER_CHARS, err)

    def test_one_megabyte_on_the_command_line(self):
        self.write_registry(self.base())
        before = self.read_bytes(self.path("filaments.json"))
        rec = ["record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", "x"]
        cases = [rec + [opt, BIG] for opt in ("--date", "--tile", "--settings", "--photos", "--note", "--why")]
        cases += [rec + [opt, "k=" + BIG] for opt in ("--feature", "--set-filament", "--set-process")]
        cases += [["record", BIG, "--printer", "P1S", "--verdict", "x"], ["record", "PLA-NAVY-01", "--printer", BIG,
                  "--verdict", "x"], ["record", "PLA-NAVY-01", "--printer", "P1S", "--verdict", BIG],
                  ["spec", BIG, "--printer", "P1S"], ["spec", "PLA-NAVY-01", "--printer", BIG], ["show", BIG]]
        add = ["add", "PLA-RED-01", "--material", "PLA", "--color", "Red", "--hex", "#FF0000"]
        cases += [add + [opt, BIG] for opt in ("--brand", "--line", "--inventory", "--drying")]
        cases += [add + ["--preset", "P1S=" + BIG], ["add", BIG, "--material", "PLA", "--color", "R", "--hex", "#FF0000"],
                  ["add", "PLA-RED-01", "--material", BIG, "--color", "R", "--hex", "#FF0000"],
                  ["add", "PLA-RED-01", "--material", "PLA", "--color", BIG, "--hex", "#FF0000"],
                  ["add", "PLA-RED-01", "--material", "PLA", "--color", "R", "--hex", BIG],
                  ["init", "--force", "--printer", "P1S=" + BIG], ["init", "--force", "--printer", BIG + "=a|b|c"]]
        for args in cases:
            label = " ".join(a if len(a) < 40 else "<1 MB>" for a in args)
            with self.subTest(label):
                code, out, err, dt = self.main(*args)
                self.assertNotEqual(code, 0, label)
                self.assertIn("characters (max %d)" % registry.MAX_TEXT_CHARS, err)
                self.assertLess(dt, 1.0)
                self.assertEqual(self.read_bytes(self.path("filaments.json")), before)
