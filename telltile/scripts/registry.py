"""Filament registry for the gnoshcodes tuning tile: one filaments.json per maker, tuned per printer.

Which file: --registry PATH, else env FILAMENT_REGISTRY, else filaments.json in the current folder (never this
skill's folder: the registry is the maker's own file).

  registry.py init --printer P1S="Bambu Lab P1S 0.4 nozzle|0.20mm Standard @BBL X1C|Textured PEI Plate"
  registry.py add --from new-filament.json      # {"code": "PLA-NAVY-01", "material": "PLA", "color": "Navy",
                                                #  "hex": "#1B2A4A", "preset": {"P1S": "Bambu PLA Basic @BBL P1S 0.4 nozzle"}}
  registry.py list
  registry.py show PLA-NAVY-01
  registry.py spec PLA-NAVY-01 --printer P1S                   # tune_project.py spec (project settings)
  registry.py spec PLA-NAVY-01 --printer P1S --for preset      # make_preset.py --overrides file (filament preset)
  registry.py spec PLA-NAVY-01 --printer P1S --for process-preset
  registry.py record --from record.json [--dry-run]   # {"code": "PLA-NAVY-01", "printer": "P1S", "verdict": "...",
        "feature": {"stringing": "bad"}, "set_filament": {"filament_retraction_length": "1.0"}, "status": "tuned"}
        (optional keys: date, tile, settings, photos, note, why, set_process)
  registry.py validate                                          # schema, codes, printers, tests, preset names
  registry.py tile PLA-NAVY-01 --printer P1S [--layer 0.20] [--out tile.stl]   # the openscad command, quoted

Where the settings go (see the reading guide):
  filament block -> a filament user preset (make_preset.py --type filament --base <presets[printer]>) or the
                    project's filament settings (tune_project.py "plate" spec). Never a process preset.
  process block  -> the project (tune_project.py "plate" spec) or a process user preset named after the filament.
`spec` writes JSON to stdout (use > file.json); hints go to stderr. Python 3.8+, standard library only.

Free text (verdicts, notes, brand, line, color, anything read off a photo or a spool label) never goes on a command
line, where a shell would expand $(...), backticks or %VAR% in it before this script runs: write it to a JSON file
and pass --from FILE. Every field of `add` and `record` can come from that file (the command-line flags remain for
a person typing their own values). The file is read with the same JSON checks as a registry, its keys and types
are checked, and the result is written only if the whole registry still passes the schema.

A registry can come from someone else, so it is a stranger's data: nothing here reads, prints or writes it before
check() passes it against SCHEMA, one closed schema (every key, type, length, list size, range and enum; the
tuning keys and their value ranges are the ones references/registry.md lists). Anything off it refuses the whole
file. post_process and every *_gcode key are always refused. Every write goes through commit(), which runs the
same check first. Every save keeps the previous file as <registry>.bak (same permissions); a registry path that
is a symlink or not a regular file is never backed up or replaced.
"""
import argparse, datetime, glob, itertools, json, math, os, re, shlex, stat, sys, tempfile, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
REG = None
_DROP = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")    # control, format, surrogate, private, unassigned, separators

# ==================================================================== SCHEMA (begin)
# Every pattern and bound on registry data lives in this section (tests/test_schema_fuzz.py checks that none sits
# outside it). Patterns are always used with fullmatch and end in \Z (never $, which also matches before "\n").
MAX_REGISTRY_BYTES = 2 * 1024 * 1024     # a heavy registry is under 1 MB; a larger file is refused, not parsed
MAX_NODES = 200000                       # JSON values check() walks; past this the file is refused
MAX_NUMBER_CHARS = 40                    # digits of a JSON number (int() is quadratic in digits on Python < 3.11)
MAX_TEXT_CHARS = 500                     # any one command-line argument, before anything parses it
MAX_VALUE_CHARS = 32                     # a setting value given as a string
MAX_PROBLEMS = 50                        # problems listed; check() stops there
MAX_FROM_BYTES = 64 * 1024               # an add/record --from file (a few hundred bytes of fields)
MAX_DEPTH = 16                           # nested objects/lists before json.loads (the schema's deepest is 8;
                                         # the margin lets check() name the path of a container one level off)
# For the depth pre-scan before json.loads (json_depth_problem): once escapes are gone, a string is a quote, no
# quotes, a quote. Both patterns repeat one character class only, so sre keeps no backtracking marks: linear time,
# no memory per character (a string pattern with a repeated group was quadratic, then held 230 MB for 2 MB).
JSON_STRING_RE = re.compile(r'"[^"]*"')
NOT_BRACKET_RE = re.compile(r'[^\[\]{}]+')
SCHEMA_ID = "gnoshcodes filament registry v1"

# SAFE: nothing special inside double quotes in bash, cmd or PowerShell, and no leading "-" (not an option).
# "/" is in it because real Bambu system preset names use it ("Bambu Support For PLA/PETG @BBL X1C").
SAFE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._@()+/-]{0,99}\Z")
PRINTER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}\Z")
CODE_RE = re.compile(r"[A-Z0-9]{1,16}-[A-Z0-9]{1,20}-[0-9][0-9]\Z")
DATE_RE = re.compile(r"[0-9]{4}-[0-9][0-9]-[0-9][0-9]\Z")
DATE_OR_EMPTY_RE = re.compile(r"(?:[0-9]{4}-[0-9][0-9]-[0-9][0-9])?\Z")
HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\Z")
TEST_ID_RE = re.compile(r"T-[0-9]{8}-[A-Za-z0-9_.-]{1,32}-[0-9]{2,4}\Z")
FEATURE_KEY_RE = re.compile(r"[a-z0-9_]{1,32}\Z")
FEATURE_VALUE_RE = re.compile(r"[A-Za-z0-9._%+ -]{1,32}\Z")
INT_RE = re.compile(r"-?[0-9]{1,6}\Z")
NUM_RE = re.compile(r"-?[0-9]{1,5}(\.[0-9]{1,5})?\Z")
PCT_RE = re.compile(r"[0-9]{1,3}(\.[0-9]{1,2})?%\Z")


# Node constructors: the schema is data built from these tuples; check() is the only code that reads them.
def TEXT(n):                        return ("text", n)
def PAT(rx, n, what):               return ("re", rx, n, what)
def ENUM(*vals):                    return ("enum", vals)
def CONST(v):                       return ("const", v)
def OBJ(req, opt=None):             return ("obj", req, opt or {})
def MAP(key, val, n):               return ("map", key, val, n)
def LIST(item, n):                  return ("list", item, n)
def SETTINGS_OF(block, nodes):      return ("set", block, nodes)
BOOL = ("bool",)


# Setting value rules (VALUE(key)): a JSON number or a string of the key's kind, then range-checked. true/false,
# NaN and Infinity are refused everywhere. Sources: Bambu Studio src/libslic3r/PrintConfig.cpp at 23a3375 for types,
# enum names and which keys are percents; hardware limits (300 C hotend, 110 C bed, 20 000 mm/s2) for ranges.
def INT(lo, hi, nil=False):         return {"int": (lo, hi), "nil": nil}
def NUM(lo, hi, nil=False):         return {"num": (lo, hi), "nil": nil}
def PCT(lo, hi):                    return {"pct": (lo, hi), "nil": False}
def NUM_PCT(lo, hi, plo, phi):      return {"num": (lo, hi), "pct": (plo, phi), "nil": False}
def FLAG(nil=False):                return {"bool": True, "nil": nil}
def ONE_OF(*vals):                  return {"enum": vals, "nil": False}


_PLATES = ("cool", "eng", "hot", "textured", "supertack")
FILAMENT_RULES = dict(
    [("nozzle_temperature", INT(165, 300)), ("nozzle_temperature_initial_layer", INT(165, 300))]
    + [("%s_plate_temp%s" % (p, s), INT(0, 110)) for p in _PLATES for s in ("", "_initial_layer")],
    filament_flow_ratio=NUM(0.8, 1.2), pressure_advance=NUM(0, 0.2), enable_pressure_advance=FLAG(),
    filament_retraction_length=NUM(0, 5, nil=True), filament_retraction_speed=NUM(5, 100, nil=True),
    filament_wipe=FLAG(nil=True), fan_min_speed=INT(0, 100), fan_max_speed=INT(10, 100),
    overhang_fan_speed=INT(10, 100), overhang_fan_threshold=ONE_OF("0%", "10%", "25%", "50%", "75%", "95%"),
    slow_down_layer_time=INT(0, 30), filament_max_volumetric_speed=NUM(1, 40))
PROCESS_RULES = dict(
    [("overhang_%d_4_speed" % i, NUM(0, 300)) for i in (1, 2, 3, 4)],
    xy_hole_compensation=NUM(-0.5, 0.5), xy_contour_compensation=NUM(-0.5, 0.5),
    elefant_foot_compensation=NUM(0, 0.5), bridge_flow=NUM(0.5, 1.5), bridge_speed=NUM(5, 150),
    overhang_totally_speed=NUM(0, 300), thick_bridges=FLAG(), enable_overhang_speed=FLAG(),
    detect_thin_wall=FLAG(), enable_arc_fitting=FLAG(),
    seam_position=ONE_OF("nearest", "aligned", "back", "random"), seam_gap=PCT(0, 50),
    seam_slope_type=ONE_OF("none", "external", "all"), top_shell_layers=INT(0, 20),
    top_surface_pattern=ONE_OF("concentric", "zig-zag", "monotonic", "monotonicline", "globalmonotonicline",
                               "alignedrectilinear", "hilbertcurve", "archimedeanchords", "octagramspiral"),
    ironing_type=ONE_OF("no ironing", "top", "topmost", "solid"), ironing_flow=PCT(0, 100),
    wall_generator=ONE_OF("classic", "arachne"), min_feature_size=PCT(5, 100), outer_wall_speed=NUM(10, 500),
    small_perimeter_speed=NUM_PCT(10, 500, 10, 100), initial_layer_speed=NUM(10, 300),
    outer_wall_acceleration=NUM(500, 20000), outer_wall_line_width=NUM(0.1, 1.2), resolution=NUM(0.001, 0.1))
RULES = {"filament": FILAMENT_RULES, "process": PROCESS_RULES}

# Per-printer caps. The ranges above are the most capable printer's; a value stored for a printer must also be
# within that printer's own plate and acceleration limit. Whether Bambu Studio or the firmware clamps a higher
# value is not verified, so the registry refuses it instead. The printer is told apart by its machine preset name
# (first prefix that matches); any other machine preset gets the lowest limits.
PRINTER_LIMITS = (                       # machine preset prefix, max plate temperature (C), max acceleration (mm/s2)
    ("Bambu Lab A1 mini", 80, 10000),
    ("Bambu Lab A1", 100, 10000),
    ("Bambu Lab P1", 100, 20000),        # P1S, P1P
    ("Bambu Lab X1", 110, 20000),        # X1, X1 Carbon, X1E
    ("Bambu Lab H2D", 110, 20000))
LOWEST_LIMITS = ("an unlisted printer", 80, 10000)
PLATE_KEYS = tuple(k for k in FILAMENT_RULES if "_plate_temp" in k)
ACCEL_KEYS = ("outer_wall_acceleration",)

TEST_ID = PAT(TEST_ID_RE, 64, "a test id T-YYYYMMDD-PRINTER-NN")
PRINTER = PAT(PRINTER_RE, 32, "a printer name (1-32 letters, digits, . _ -, starting with a letter or digit)")
SAFE = PAT(SAFE_RE, 100, "a preset or plate name (1-100 letters, digits, spaces, . _ @ ( ) + / -, starting with "
                         "a letter or digit)")
CODE = PAT(CODE_RE, 40, "a code MATERIAL-COLOR-NN")
DATE = PAT(DATE_RE, 10, "a date YYYY-MM-DD")
SETTING = {b: SETTINGS_OF(b, {k: OBJ({"value": ("value", k, r), "tests": LIST(TEST_ID, 200)}, {"why": TEXT(500)})
                              for k, r in RULES[b].items()}) for b in RULES}
CHANGED = {b: SETTINGS_OF(b, {k: ("value", k, r) for k, r in RULES[b].items()}) for b in RULES}
TEST = OBJ({"id": TEST_ID, "date": DATE, "printer": PRINTER},
           {"tile": TEXT(100), "photos": TEXT(100), "settings": TEXT(500), "verdict": TEXT(500), "note": TEXT(500),
            "changed": OBJ({"filament": CHANGED["filament"], "process": CHANGED["process"]}),
            "features": MAP(PAT(FEATURE_KEY_RE, 32, "a feature name (1-32 of a-z 0-9 _)"),
                            PAT(FEATURE_VALUE_RE, 32, "a feature result (1-32 letters, digits, spaces, . _ % + -)"),
                            40)})
FILAMENT = OBJ({"code": CODE, "brand": TEXT(64), "product_line": TEXT(64), "material": TEXT(64),
                "inventory": TEXT(200), "drying": TEXT(200),
                "color": OBJ({"name": TEXT(64), "hex": PAT(HEX_RE, 7, "a color #RRGGBB"), "hex_is_estimate": BOOL}),
                "diameter_mm": ENUM(1.75, 2.85), "presets": MAP(PRINTER, SAFE, 16),
                "tuned": MAP(PRINTER, OBJ({"status": ENUM("untested", "baseline", "tuned"),
                                           "filament": SETTING["filament"], "process": SETTING["process"]}), 16),
                "tests": LIST(TEST, 200)})
SCHEMA = OBJ({"schema": CONST(SCHEMA_ID), "updated": PAT(DATE_OR_EMPTY_RE, 10, "a date YYYY-MM-DD or empty"),
              "printers": MAP(PRINTER, OBJ({"machine_preset": SAFE, "process_preset": SAFE, "plate": SAFE,
                                            "nozzle": ENUM("0.2", "0.4", "0.6", "0.8")}), 16),
              "filaments": LIST(FILAMENT, 500)},
             {"notes": LIST(TEXT(500), 50)})
# ==================================================================== SCHEMA (end)

FILAMENT_KEYS = frozenset(FILAMENT_RULES)
PROCESS_KEYS = frozenset(PROCESS_RULES)
BLOCK_KEYS = {"filament": FILAMENT_KEYS, "process": PROCESS_KEYS}


def safe_text(s, limit=200):
    """One printable line from a registry string: control and format characters (escape sequences, C1 controls,
    bidi overrides) become '?', so `list` and error messages cannot drive the terminal."""
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    t = "".join("?" if unicodedata.category(ch) in _DROP else ch for ch in s[:limit + 1])
    return t if len(t) <= limit else t[:limit - 3] + "..."


def key_problem(k, block):
    """Why setting key k may not be stored in (or printed from) the block, or None."""
    kl = k.lower()
    if "post_process" in kl:
        return "post_process is never accepted (it runs a program on the computer that slices)"
    if kl.endswith("_gcode") or "gcode" in kl:
        return "G-code settings are never accepted (start, end and layer G-code run on the printer)"
    if k in BLOCK_KEYS[block]:
        return None
    other = "process" if block == "filament" else "filament"
    if k in BLOCK_KEYS[other]:
        return "is a %s setting, not a %s one (references/registry.md)" % (other, block)
    return "is not a tuning setting this registry accepts (see references/registry.md)"


def _g(x):
    return "%g" % x


def rule_text(rule):
    """A value rule in words, as references/registry.md lists it: "int 165..300", "pct 0..50%", "num 0..5 or nil"."""
    parts = []
    if "int" in rule:
        parts.append("int %s..%s" % tuple(map(_g, rule["int"])))
    if "num" in rule:
        parts.append("num %s..%s" % tuple(map(_g, rule["num"])))
    if "pct" in rule:
        parts.append("pct %s..%s%%" % tuple(map(_g, rule["pct"])))
    if "bool" in rule:
        parts.append("bool 0/1")
    if "enum" in rule:
        parts.append("one of " + ", ".join(rule["enum"]))
    if rule["nil"]:
        parts.append("nil")
    return " or ".join(parts)


def _in(x, lohi):
    return lohi[0] <= x <= lohi[1]


def value_problem(v, rule):
    """Why v is not a value of this setting rule, or None. Strings are bounded before any pattern or float()."""
    if isinstance(v, bool):
        return "true/false is not accepted (a switch is 0 or 1)"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return "must be a finite number"
        if "bool" in rule:
            ok = isinstance(v, int) and v in (0, 1)
        elif "int" in rule:
            ok = isinstance(v, int) and _in(v, rule["int"])
        elif "num" in rule:
            ok = _in(v, rule["num"])
        else:
            return "a JSON number is not accepted here; use %s" % rule_text(rule)
        return None if ok else "%s is not %s" % (_g(v), rule_text(rule))
    if not isinstance(v, str):
        return "must be a number or a short string, not %s" % _json_type(v)
    if len(v) > MAX_VALUE_CHARS:
        return "a string of %d characters (max %d)" % (len(v), MAX_VALUE_CHARS)
    ok = False
    if v == "nil":
        ok = rule["nil"]
    elif "enum" in rule:
        ok = v in rule["enum"]
    elif "bool" in rule:
        ok = v in ("0", "1")
    elif "pct" in rule and v.endswith("%"):
        ok = bool(PCT_RE.fullmatch(v)) and _in(float(v[:-1]), rule["pct"])
    elif "int" in rule:
        ok = bool(INT_RE.fullmatch(v)) and _in(int(v), rule["int"])
    elif "num" in rule:
        ok = bool(NUM_RE.fullmatch(v)) and _in(float(v), rule["num"])
    return None if ok else "%r is not %s" % (safe_text(v, 40), rule_text(rule))


def _json_type(v):
    if isinstance(v, bool):
        return "true/false"
    for typ, name in ((dict, "an object"), (list, "a list"), (str, "a string"), ((int, float), "a number")):
        if isinstance(v, typ):
            return name
    return "null" if v is None else type(v).__name__


def path_text(trail):
    """'filaments[0].tuned.P1S.filament.nozzle_temperature.value' from a trail (parent trail, key) chain. Only keys
    check() has already accepted are in a trail, so they are plain; a rejected key is named in the problem itself."""
    keys = []
    while trail is not None:
        trail, k = trail
        keys.append(k)
    out = ""
    for k in reversed(keys):
        out += "[%d]" % k if isinstance(k, int) else ("." if out else "") + k
    return out or "(top)"


_EXPECT = {"text": "a string", "re": "a string", "const": "a string", "bool": "true or false", "obj": "an object",
           "map": "an object", "set": "an object", "list": "a list"}
_TYPES = {"text": str, "re": str, "const": str, "obj": dict, "map": dict, "set": dict, "list": list}


def _shown(v):
    """A value for a problem message: strings shortened and made printable, others by JSON type or number."""
    if isinstance(v, str):
        return repr(safe_text(v, 40))
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return _g(v)
    return _json_type(v)


def check(reg):
    """Problems with reg against SCHEMA (at most MAX_PROBLEMS); [] means it may be read, printed and written.
    Iterative (a stack of schema node, value, trail), so no nesting depth can overflow it. A container where the
    schema wants a scalar (or a list or map over its cap) is refused without descending into it. Paths are
    formatted only for a reported problem."""
    out = []

    def bad(trail, msg):
        out.append("%s: %s" % (path_text(trail), msg))

    stack = [(SCHEMA, reg, None)]
    nodes = 0
    while stack and len(out) < MAX_PROBLEMS:
        node, v, trail = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            out.append("(top): over %d values; a registry has a few thousand" % MAX_NODES)
            break
        kind = node[0]
        if kind == "bool":
            if not isinstance(v, bool):
                bad(trail, "must be true or false, not %s" % _json_type(v))
            continue
        typ = _TYPES.get(kind)
        if typ is not None and not isinstance(v, typ):
            bad(trail, "must be %s, not %s" % (_EXPECT[kind], _json_type(v)))
            continue
        if kind == "text":
            if len(v) > node[1]:
                bad(trail, "a string of %d characters (max %d)" % (len(v), node[1]))
            elif any(unicodedata.category(ch) in _DROP for ch in v):
                bad(trail, "holds a control or format character (refused, not escaped)")
        elif kind == "re":
            if len(v) > node[2]:
                bad(trail, "a string of %d characters (max %d)" % (len(v), node[2]))
            elif not node[1].fullmatch(v):
                bad(trail, "%s is not %s" % (_shown(v), node[3]))
        elif kind == "const":
            if v != node[1]:
                bad(trail, "must be %r" % node[1])
        elif kind == "enum":
            if isinstance(v, bool) or not any(type(v) is type(x) and v == x for x in node[1]):
                bad(trail, "%s is not one of %s" % (_shown(v), ", ".join(map(str, node[1]))))
        elif kind == "value":
            why = value_problem(v, node[2])
            if why:
                bad(trail, "%s (%s)" % (why, node[1]))
        elif kind == "list":
            if len(v) > node[2]:
                bad(trail, "%d entries (max %d)" % (len(v), node[2]))
                continue
            for i in range(len(v) - 1, -1, -1):
                stack.append((node[1], v[i], (trail, i)))
        elif kind == "obj":
            req, opt = node[1], node[2]
            for k in v:
                if k not in req and k not in opt:
                    bad(trail, "unknown key %r" % safe_text(k, 40))
            for k in req:
                if k not in v:
                    bad(trail, "missing %r" % k)
            for k in reversed(list(v)):
                sub = req.get(k) or opt.get(k)
                if sub is not None:
                    stack.append((sub, v[k], (trail, k)))
        elif kind == "map":
            if len(v) > node[3]:
                bad(trail, "%d entries (max %d)" % (len(v), node[3]))
                continue
            keynode = node[1]
            for k in reversed(list(v)):
                if len(k) > keynode[2]:
                    bad(trail, "a key of %d characters (max %d)" % (len(k), keynode[2]))
                elif not keynode[1].fullmatch(k):
                    bad(trail, "key %s is not %s" % (_shown(k), keynode[3]))
                else:
                    stack.append((node[2], v[k], (trail, k)))
        elif kind == "set":
            for k in reversed(list(v)):
                why = key_problem(k, node[1])
                if why:
                    bad(trail, "%s: %s" % (safe_text(k, 80), why))
                else:
                    stack.append((node[2][k], v[k], (trail, k)))
    if not out:
        limit_problems(reg, out)
    return out


def printer_limits(reg, name):
    """(what, max plate C, max acceleration) for the printer of that name in a registry check() has walked."""
    machine = reg["printers"].get(name, {}).get("machine_preset", "")
    for prefix, plate, accel in PRINTER_LIMITS:
        if machine.startswith(prefix):
            return prefix, plate, accel
    return LOWEST_LIMITS


def limit_problems(reg, out):
    """Per-printer caps on the values a structurally valid registry stores: every tuned value and every recorded
    change, against the printer it belongs to. Bounded: only the 13 capped keys of each block are looked at."""
    def over(where, block, printer, tuned):
        what, plate, accel = printer_limits(reg, printer)
        for keys, cap, unit, name in ((PLATE_KEYS, plate, "C", "plate"), (ACCEL_KEYS, accel, "mm/s2", "acceleration")):
            for k in keys:
                if k in block:
                    v = block[k]["value"] if tuned else block[k]      # check() passed it: a number or its string
                    if float(v) > cap:
                        out.append("%s.%s%s: %s is over the %s limit for %s (%s %s)"
                                   % (where, k, ".value" if tuned else "", _shown(v), name, what, _g(cap), unit))
    for fi, f in enumerate(reg["filaments"]):
        for pr, t in f["tuned"].items():
            for b in ("filament", "process"):
                over("filaments[%d].tuned.%s.%s" % (fi, pr, b), t[b], pr, True)
        for ti, x in enumerate(f["tests"]):
            ch = x.get("changed")
            if ch:
                for b in ("filament", "process"):
                    over("filaments[%d].tests[%d].changed.%s" % (fi, ti, b), ch[b], x["printer"], False)
        if len(out) >= MAX_PROBLEMS:
            del out[MAX_PROBLEMS:]
            return


def _json_number(text):
    if len(text) > MAX_NUMBER_CHARS:
        raise ValueError("a number of %d digits (max %d)" % (len(text), MAX_NUMBER_CHARS))
    return text


def _no_constant(name):
    raise ValueError("%s is not a number a registry may hold" % name)


def _no_duplicates(pairs):
    d = dict(pairs)
    if len(d) != len(pairs):
        seen = set()
        for k, _ in pairs:
            if k in seen:
                raise ValueError("duplicate key %r" % safe_text(k, 40))
            seen.add(k)
    return d


def json_depth_problem(data):
    """Refuse nesting deeper than MAX_DEPTH before json.loads sees it. json.loads recurses once per level; on
    Python 3.8 a deep file parsed in an already deep call stack can abort the interpreter ("Fatal Python error:
    Cannot recover from stack overflow") instead of raising RecursionError.
    A reduction done in C, linear in time and memory (no per-character Python loop, no regex backtracking):
    drop escaped pairs (\\\\ first, then \\", so every quote left delimits a string), drop the strings with the
    brackets in them, keep only brackets, and take the deepest running count, 64 K brackets at a time. Up to the
    first error json.loads would hit, the text is valid JSON and this count is exact; text past that point only
    adds steps after it (json.loads never parses it), so a prefix nested too deep is always refused."""
    s = data.replace("\\\\", "").replace('\\"', "")
    s = NOT_BRACKET_RE.sub("", JSON_STRING_RE.sub("", s))
    depth, step = 0, _BRACKET_STEP.__getitem__
    for i in range(0, len(s), 65536):
        part = s[i:i + 65536]
        if max(itertools.accumulate(map(step, part), initial=depth)) > MAX_DEPTH:
            return "nested deeper than %d levels (a registry has 8)" % MAX_DEPTH
        depth += sum(map(step, part))
    return None


_BRACKET_STEP = {"[": 1, "{": 1, "]": -1, "}": -1}


def parse_registry(data):
    """json.loads with nesting capped first, every number's length checked before it is converted, NaN/Infinity
    and duplicate keys refused. The result still has to pass check()."""
    why = json_depth_problem(data)
    if why:
        raise ValueError(why)
    return json.loads(data, parse_int=lambda t: int(_json_number(t)), parse_float=lambda t: float(_json_number(t)),
                      parse_constant=_no_constant, object_pairs_hook=_no_duplicates)


def registry_path(arg):
    return arg or os.environ.get("FILAMENT_REGISTRY") or os.path.join(os.getcwd(), "filaments.json")


def known_keys():
    """Setting-key list from the bambu-studio-settings skill, if it sits next to this one (typo check)."""
    for p in (os.environ.get("BAMBU_KNOWN_KEYS"),
              os.path.join(SKILL_ROOT, "..", "bambu-studio-settings", "assets", "known_setting_keys.txt")):
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                return set(l.strip() for l in fh if l.strip())
    return None


def profiles_dir():
    for p in (os.environ.get("BAMBU_STUDIO_PROFILES"), r"C:\Program Files\Bambu Studio\resources\profiles",
              "/Applications/BambuStudio.app/Contents/Resources/profiles"):
        if p and os.path.isdir(os.path.join(p, "BBL")):
            return os.path.join(p, "BBL")
    return None


def read_capped(path):
    if os.path.getsize(path) > MAX_REGISTRY_BYTES:
        sys.exit("refused: %s is over %d MB; a filament registry is a few KB" % (path, MAX_REGISTRY_BYTES // 2 ** 20))
    with open(path, "rb") as fh:
        data = fh.read(MAX_REGISTRY_BYTES + 1)
    if len(data) > MAX_REGISTRY_BYTES:
        sys.exit("refused: %s is over %d MB" % (path, MAX_REGISTRY_BYTES // 2 ** 20))
    return data


def read_for_backup(path):
    """(bytes, permission bits) of the registry about to be replaced, for its .bak copy. Only a regular file is
    copied: a symlink (or a device or fifo) at the registry path is refused, never followed, so a link to some
    other file cannot have that file copied into a .bak. Opened with O_NOFOLLOW where the system has it, and
    checked with fstat on the open file, so the check and the read see the same file."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        sys.exit("refused: %s is a symlink or not a regular file; registry.py only replaces a plain file "
                 "(use --registry with the real file's path)" % path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            sys.exit("refused: %s is not a regular file" % path)
        if st.st_size > MAX_REGISTRY_BYTES:
            sys.exit("refused: %s is over %d MB; a filament registry is a few KB" % (path, MAX_REGISTRY_BYTES // 2 ** 20))
        data = fh.read(MAX_REGISTRY_BYTES + 1)
    if len(data) > MAX_REGISTRY_BYTES:
        sys.exit("refused: %s is over %d MB" % (path, MAX_REGISTRY_BYTES // 2 ** 20))
    return data, stat.S_IMODE(st.st_mode)


def refuse(problems, what):
    """Print the problems (at most MAX_PROBLEMS, already capped by check) to stderr and exit non-zero."""
    for p in problems[:MAX_PROBLEMS]:
        print("  " + p, file=sys.stderr)
    if len(problems) >= MAX_PROBLEMS:
        print("  (stopped at %d problems)" % MAX_PROBLEMS, file=sys.stderr)
    sys.exit("refused: %s" % what)


def load():
    """The registry at REG, parsed and passed against SCHEMA, or exit. Nothing else reads the file."""
    if not os.path.exists(REG):
        sys.exit("no registry at %s (registry.py init, or --registry PATH)" % REG)
    try:
        reg = parse_registry(read_capped(REG).decode("utf-8"))
    except (ValueError, RecursionError) as e:
        sys.exit("%s is not a registry (bad JSON: %s)" % (REG, safe_text(str(e), 120)))
    bad = check(reg)
    if bad:
        refuse(bad, "%s is not a usable registry; nothing in it was used" % REG)
    return reg


def write_atomic(path, data, mode=None):
    """Write bytes to path via a new temp file in the same folder (mkstemp: O_EXCL, so a planted file or symlink
    is never opened or followed), then rename it over path. A symlink at path is replaced, not written through.
    The file gets `mode` if given (a .bak keeps its source's mode), else the replaced regular file's mode."""
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data); fh.flush(); os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        elif os.path.lexists(path) and stat.S_ISREG(os.lstat(path).st_mode):
            os.chmod(tmp, stat.S_IMODE(os.lstat(path).st_mode))
        else:
            um = os.umask(0); os.umask(um); os.chmod(tmp, 0o666 & ~um)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def writable_target():
    """REG as an absolute path, or exit if it is inside this skill's folder (the registry is the maker's file)."""
    target = os.path.abspath(REG)
    root = os.path.realpath(SKILL_ROOT)
    folder = os.path.realpath(os.path.dirname(target))
    if folder == root or folder.startswith(root + os.sep):
        sys.exit("refused: %s is inside the skill folder; keep filaments.json with your projects (--registry PATH)" % REG)
    return target


def commit(reg, dry_run=False):
    """The one way a registry is written: check() first, then json.dumps, then a size cap; only then (unless
    dry_run) keep the old file as .bak and replace it. Returns the bytes. Refused means nothing is written."""
    reg["updated"] = datetime.date.today().isoformat()
    bad = check(reg)
    if bad:
        refuse(bad, "the result would not be a valid registry; nothing written")
    data = (json.dumps(reg, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    if len(data) > MAX_REGISTRY_BYTES:
        sys.exit("refused: the registry would be %d bytes (max %d MB); nothing written" % (len(data),
                                                                                        MAX_REGISTRY_BYTES // 2 ** 20))
    if dry_run:
        return data
    target = writable_target()
    if os.path.lexists(target):
        old, mode = read_for_backup(target)             # refuses a symlink or non-regular file, nothing written
        write_atomic(target + ".bak", old, mode)
        write_atomic(target, data, mode)
    else:
        write_atomic(target, data)
    return data


def get(reg, code):
    """The filament with this code (any case). The argument must look like a code before it is compared."""
    if not (code.isascii() and CODE_RE.fullmatch(code.upper())):
        sys.exit("no filament %s: a code looks like MATERIAL-COLOR-NN, e.g. PLA-NAVY-01" % repr(safe_text(code, 40)))
    for f in reg["filaments"]:
        if f["code"].upper() == code.upper():
            return f
    sys.exit("no filament %s (try: registry.py list)" % code)


def printer_of(reg, name):
    if not PRINTER_RE.fullmatch(name):
        sys.exit("--printer %s is not a printer name (1-32 letters, digits, . _ -, starting with a letter or digit)"
                 % repr(safe_text(name, 40)))
    if name not in reg["printers"]:
        sys.exit("printer %s is not in this registry (have: %s)" % (name, ", ".join(reg["printers"]) or "none"))
    return name


def values(block):
    return {k: v["value"] for k, v in block.items()}


def parse_kv(items):
    out = {}
    for s in items or []:
        if "=" not in s:
            sys.exit("expected key=value, got %r" % safe_text(s, 80))
        k, v = s.split("=", 1); out[k.strip()] = v.strip()
    return out


def cmd_init(a):
    if os.path.lexists(REG) and not a.force:
        sys.exit("%s exists (use --force to start over; the old file is kept as a dated .bak)" % REG)
    printers = {}
    for k, v in parse_kv(a.printer).items():
        m, p, plate = (v.split("|") + ["", "", ""])[:3]
        printers[k] = {"machine_preset": m, "process_preset": p, "plate": plate or "Textured PEI Plate", "nozzle": "0.4"}
    reg = {"schema": SCHEMA_ID, "updated": "", "notes": [], "printers": printers, "filaments": []}
    commit(reg, dry_run=True)                          # refuse before the backup is made
    writable_target()
    if os.path.lexists(REG):
        data, mode = read_for_backup(REG)               # refuses a symlink or non-regular file, nothing written
        old = os.path.abspath(REG) + ".before-init-%s.bak" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        write_atomic(old, data, mode); print("kept the old registry as %s" % old)
    commit(reg); print("created %s with printers %s" % (REG, ", ".join(printers) or "(none)"))


def cmd_add(a):
    reg = load()
    if not CODE_RE.fullmatch(a.code):
        sys.exit("code must look like MATERIAL-COLOR-NN, e.g. PLA-NAVY-01")
    if any(f["code"].upper() == a.code.upper() for f in reg["filaments"]):
        sys.exit("%s already exists; codes are never reused - use the next number" % a.code)
    if not HEX_RE.fullmatch(a.hex):
        sys.exit("--hex must look like #1B2A4A")
    presets = pairs_of(a.preset)
    for p in presets:
        printer_of(reg, p)
    reg["filaments"].append({
        "code": a.code, "brand": a.brand, "product_line": a.line, "material": a.material,
        "color": {"name": a.color, "hex": a.hex, "hex_is_estimate": not a.hex_exact}, "diameter_mm": 1.75,
        "inventory": a.inventory, "drying": a.drying or "", "presets": presets,
        "tuned": {p: {"status": "untested", "filament": {}, "process": {}} for p in reg["printers"]}, "tests": []})
    commit(reg); print("added %s" % a.code)


def cmd_list(a):
    reg = load(); ps = list(reg["printers"])
    S = lambda v, n=60: safe_text(v, n)
    print("%-15s %-6s %-10s %-9s %s  %s" % ("code", "mat", "color", "hex", "  ".join("%-9s" % S(p, 20) for p in ps), "brand / line"))
    for f in reg["filaments"]:
        st = "  ".join("%-9s" % S(f["tuned"].get(p, {}).get("status", "-"), 20) for p in ps)
        print("%-15s %-6s %-10s %-9s %s  %s / %s" % (S(f["code"], 40), S(f.get("material")), S(f["color"].get("name")),
                                                     S(f["color"].get("hex"), 20), st, S(f.get("brand")), S(f.get("product_line"))))


def cmd_show(a):
    print(json.dumps(get(load(), a.code), indent=1))   # ASCII-escaped: no raw control characters reach the terminal


def cmd_spec(a):
    """The spec on stdout, a command hint on stderr. Every registry value in a hint is shlex-quoted (POSIX shells,
    Git Bash on Windows); the schema's SAFE names also stay inert inside double quotes in cmd and PowerShell."""
    reg = load(); f = get(reg, a.code); pr = printer_of(reg, a.printer)
    if pr not in f["tuned"]:
        sys.exit("%s has no tuned entry for %s" % (f["code"], pr))
    t = f["tuned"][pr]
    fil, proc = values(t["filament"]), values(t["process"])
    q = shlex.quote
    base = f["presets"].get(pr)
    base_q = q(base) if base else '"<system filament preset>"'
    if a.for_ == "tune":
        plate = dict(fil); plate.update(proc); plate["filament_color"] = f["color"]["hex"]
        print(json.dumps({"plate": plate}, indent=1))
        print("# tune_project.py in.3mf out.3mf --spec <this file>   (%s on %s, status %s; base filament preset %s)"
              % (f["code"], pr, t["status"], base_q), file=sys.stderr)
    elif a.for_ == "preset":
        print(json.dumps(fil, indent=1))
        print('# make_preset.py --user-dir "<BambuStudio user folder>/user/<user_id>" --type filament --base %s '
              '--name %s --overrides <this file>   (dry run first; a user preset syncs to the Bambu account)'
              % (base_q, q("%s %s" % (f["code"], pr))), file=sys.stderr)
    else:
        print(json.dumps(proc, indent=1))
        pb = reg["printers"][pr]["process_preset"]
        print("# make_preset.py ... --type process --base %s --name %s --overrides <this file>"
              % (q(pb), q("%s %s" % (pb.split(" @")[0], f["code"]))), file=sys.stderr)
    what, plate, accel = printer_limits(reg, pr)
    print("# limits checked for %s: %s (plate <= %s C, outer wall acceleration <= %s mm/s2), from its machine_preset "
          "%s; for a registry from someone else, confirm this is the maker's printer"
          % (pr, what, _g(plate), _g(accel), shlex.quote(reg["printers"][pr]["machine_preset"])), file=sys.stderr)
    if t["status"] == "untested":
        print("# note: %s is untested on %s - the spec holds no tuned values yet" % (f["code"], pr), file=sys.stderr)


def cmd_record(a):
    reg = load(); f = get(reg, a.code); pr = printer_of(reg, a.printer)
    date = a.date or datetime.date.today().isoformat()
    if not DATE_RE.fullmatch(date):
        sys.exit("--date must be YYYY-MM-DD")
    t = f["tuned"].setdefault(pr, {"status": "untested", "filament": {}, "process": {}})
    stem = "T-%s-%s-" % (date.replace("-", ""), pr)
    same = re.compile(re.escape(stem) + r"([0-9]{2,4})\Z")     # id numbering: T-<date>-P1S-NN, not T-<date>-P1S-2-NN
    tid = stem + "%02d" % (1 + max([int(m.group(1)) for m in (same.fullmatch(x["id"]) for x in f["tests"]) if m] or [0]))
    setf, setp = pairs_of(a.set_filament), pairs_of(a.set_process)
    rec = {"id": tid, "date": date, "printer": pr, "tile": a.tile,
           "settings": a.settings or "%s + %s + registry overrides as of this date" % (
               reg["printers"][pr]["process_preset"], f["presets"].get(pr, "?")),
           "changed": {"filament": setf, "process": setp}, "photos": a.photos or "",
           "features": pairs_of(a.feature), "verdict": a.verdict, "note": a.note or ""}
    f["tests"].append(rec)
    for block, kv in (("filament", setf), ("process", setp)):
        for k, v in kv.items():
            old = t[block].get(k, {})
            t[block][k] = {"value": v, "tests": (old.get("tests", []) if old.get("value") == v else []) + [tid],
                           "why": a.why or a.verdict}
    t["status"] = a.status or ("baseline" if t["status"] == "untested" else t["status"])
    commit(reg, dry_run=True)                          # the whole result passes SCHEMA, or nothing happens
    kk = known_keys()
    if kk:
        bad = [k for k in list(setf) + list(setp) if k not in kk]
        if bad:
            sys.exit("unknown Bambu Studio setting key(s): %s" % ", ".join(bad))
    if a.dry_run:
        print(json.dumps(rec, indent=1)); print("(dry run - nothing written)"); return
    commit(reg)
    print("recorded %s for %s on %s; status %s" % (tid, f["code"], pr, t["status"]))


def cmd_validate(a):
    """load() (the schema: safety never depends on what follows) plus cross-references: duplicate codes, unknown
    printers, cited tests that exist, a test behind every value, preset names in this install, known keys."""
    reg = load(); problems = []
    codes = [f["code"] for f in reg["filaments"]]
    if len(set(c.upper() for c in codes)) != len(codes):
        problems.append("duplicate codes")
    prof = profiles_dir(); names = None
    if prof:
        names = set()
        for p in glob.glob(os.path.join(prof, "filament", "*.json")):
            try:
                with open(p, encoding="utf-8") as fh:
                    names.add(json.load(fh).get("name"))
            except Exception:
                pass
    kk = known_keys()
    for f in reg["filaments"]:
        c = f["code"]
        ids = {x["id"] for x in f["tests"]}
        for p, preset in f["presets"].items():
            if p not in reg["printers"]: problems.append("%s: preset for unknown printer %s" % (c, p))
            if names is not None and preset not in names:
                problems.append("%s: preset not in this Bambu Studio install: %s" % (c, preset))
        for x in f["tests"]:
            if x["printer"] not in reg["printers"]:
                problems.append("%s test %s: unknown printer %s" % (c, x["id"], x["printer"]))
        for p, t in f["tuned"].items():
            if p not in reg["printers"]: problems.append("%s: tuned entry for unknown printer %s" % (c, p))
            for block in ("filament", "process"):
                for k, v in t[block].items():
                    if kk and k not in kk: problems.append("%s %s: unknown key %s" % (c, p, k))
                    if not v["tests"]: problems.append("%s %s: %s has no test behind it" % (c, p, k))
                    for tid in v["tests"]:
                        if tid not in ids: problems.append("%s %s: %s cites missing test %s" % (c, p, k, tid))
    if problems:
        print("\n".join(problems[:MAX_PROBLEMS]))
        if len(problems) > MAX_PROBLEMS: print("... and %d more" % (len(problems) - MAX_PROBLEMS))
    else:
        print("registry OK: %d filaments (%s)" % (len(codes), REG))
    sys.exit(1 if problems else 0)


def cmd_tile(a):
    """The openscad command that makes a tile with this ID, every argument shlex-quoted, on stdout. The values
    must match the schema's CODE and PRINTER patterns (letters, digits, - _ .), so nothing in them is special
    inside an OpenSCAD string or a shell; the agent runs this command and never writes -D strings itself. Needs
    no registry: a tile can be made before its filament is added."""
    defines = []
    if a.printer is not None:
        if not PRINTER_RE.fullmatch(a.printer):
            sys.exit("--printer %s is not a printer name (1-32 letters, digits, . _ -, starting with a letter or "
                     "digit)" % repr(safe_text(a.printer, 40)))
        defines.append('PRINTER="%s"' % a.printer)
    if a.code is not None:
        code = a.code.upper() if a.code.isascii() else a.code
        if not CODE_RE.fullmatch(code):
            sys.exit("code %s must look like MATERIAL-COLOR-NN, e.g. PLA-NAVY-01" % repr(safe_text(a.code, 40)))
        defines.append('FIL="%s"' % code)
    if a.layer is not None:
        if not (NUM_RE.fullmatch(a.layer) and 0 < float(a.layer) <= 1):
            sys.exit("--layer must be a layer height in mm, e.g. 0.20")
        defines.append('LAYER="%s"' % a.layer)
    if not SAFE_RE.fullmatch(a.out) or not a.out.lower().endswith(".stl"):
        sys.exit("--out must be a plain file name ending in .stl (letters, digits, spaces, . _ @ ( ) + / -)")
    argv = ["openscad", "-o", a.out]
    for d in defines:
        argv += ["-D", d]
    argv.append(os.path.join(SKILL_ROOT, "assets", "tile.scad"))
    print(" ".join(shlex.quote(x) for x in argv))


# Fields an add/record --from file may hold: "text" a string, "flag" true/false, "names" an object of strings
# (preset per printer, feature results), "values" an object of strings or numbers (setting values).
FROM_FIELDS = {
    "add": {"code": "text", "material": "text", "color": "text", "hex": "text", "hex_exact": "flag",
            "brand": "text", "line": "text", "inventory": "text", "drying": "text", "preset": "names"},
    "record": {"code": "text", "printer": "text", "verdict": "text", "date": "text", "tile": "text",
               "settings": "text", "photos": "text", "note": "text", "why": "text", "status": "text",
               "feature": "names", "set_filament": "values", "set_process": "values"}}
FROM_DEFAULTS = {"add": {"brand": "unknown", "line": "unknown", "inventory": ""}, "record": {"tile": "tuning tile v1"}}
FROM_REQUIRED = {"add": ("code", "material", "color", "hex"), "record": ("code", "printer", "verdict")}


def merge_from(a):
    """Fill a's add/record fields from the --from JSON file (which the agent writes with its file tool, so no shell
    ever sees the text), then apply defaults and check the required ones. A field given both on the command line
    and in the file is refused rather than guessed."""
    path = a.from_
    if path is not None:
        if os.path.getsize(path) > MAX_FROM_BYTES:
            sys.exit("refused: --from %s is over %d KB" % (safe_text(path, 200), MAX_FROM_BYTES // 1024))
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FROM_BYTES + 1)
        if len(raw) > MAX_FROM_BYTES:
            sys.exit("refused: --from %s is over %d KB" % (safe_text(path, 200), MAX_FROM_BYTES // 1024))
        try:
            doc = parse_registry(raw.decode("utf-8-sig"))
        except (ValueError, RecursionError) as e:          # UnicodeDecodeError is a ValueError
            sys.exit("refused: --from %s is not JSON (%s)" % (safe_text(path, 200), safe_text(str(e), 120)))
        if not isinstance(doc, dict):
            sys.exit("refused: --from %s must hold one JSON object of fields" % safe_text(path, 200))
        fields = FROM_FIELDS[a.cmd]
        for k, v in doc.items():
            kind = fields.get(k)
            if kind is None:
                sys.exit("refused: --from: %r is not a field of %s (fields: %s)"
                         % (safe_text(k, 40), a.cmd, ", ".join(sorted(fields))))
            ok = (isinstance(v, str) and len(v) <= MAX_TEXT_CHARS if kind == "text" else
                  isinstance(v, bool) if kind == "flag" else
                  isinstance(v, dict) and all(
                      isinstance(x, str) and len(x) <= MAX_TEXT_CHARS and (
                          isinstance(y, str) and len(y) <= MAX_TEXT_CHARS or
                          kind == "values" and isinstance(y, (int, float)) and not isinstance(y, bool))
                      for x, y in v.items()))
            if not ok:
                want = {"text": "a string of at most %d characters" % MAX_TEXT_CHARS, "flag": "true or false",
                        "names": "an object of strings", "values": "an object of strings or numbers"}[kind]
                sys.exit("refused: --from: %s must be %s" % (k, want))
            given = getattr(a, k)
            if given not in (None, False, []):
                sys.exit("refused: %s is given both on the command line and in --from; give it once" % k)
            setattr(a, k, v)
    for k, v in FROM_DEFAULTS[a.cmd].items():
        if getattr(a, k) is None:
            setattr(a, k, v)
    missing = [k for k in FROM_REQUIRED[a.cmd] if getattr(a, k) in (None, "")]
    if missing:
        sys.exit("refused: %s needs %s (in the --from file, or on the command line)" % (a.cmd, ", ".join(missing)))
    if a.cmd == "record" and a.status not in (None, "untested", "baseline", "tuned"):
        sys.exit("refused: status must be untested, baseline or tuned")


def pairs_of(items):
    """key=value pairs from repeated command-line flags (a list) or from a --from object (a dict)."""
    return dict(items) if isinstance(items, dict) else parse_kv(items)


def main():
    global REG
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", help="path to filaments.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--printer", action="append", help='NAME="machine preset|process preset|plate"')
    s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_init)
    by_hand = "typed by a person; an agent puts it in the --from file instead"
    s = sub.add_parser("add"); s.add_argument("code", nargs="?")
    s.add_argument("--from", dest="from_", metavar="FILE.json", help="the fields as a JSON object (see the top)")
    s.add_argument("--material", help=by_hand); s.add_argument("--color", help=by_hand); s.add_argument("--hex")
    s.add_argument("--hex-exact", action="store_true")
    s.add_argument("--brand", help=by_hand); s.add_argument("--line", help=by_hand)
    s.add_argument("--inventory", help=by_hand); s.add_argument("--drying", help=by_hand)
    s.add_argument("--preset", action="append", help='PRINTER="system filament preset name"'); s.set_defaults(fn=cmd_add)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    s = sub.add_parser("show"); s.add_argument("code"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("spec"); s.add_argument("code"); s.add_argument("--printer", required=True)
    s.add_argument("--for", dest="for_", default="tune", choices=("tune", "preset", "process-preset")); s.set_defaults(fn=cmd_spec)
    s = sub.add_parser("record"); s.add_argument("code", nargs="?"); s.add_argument("--printer")
    s.add_argument("--from", dest="from_", metavar="FILE.json", help="the fields as a JSON object (see the top)")
    s.add_argument("--verdict", help=by_hand); s.add_argument("--date"); s.add_argument("--tile", help=by_hand)
    s.add_argument("--settings", help=by_hand); s.add_argument("--photos", help=by_hand)
    s.add_argument("--note", help=by_hand); s.add_argument("--why", help=by_hand)
    s.add_argument("--feature", action="append", help="zone=result, e.g. fit=0.20 or stringing=bad")
    s.add_argument("--set-filament", action="append"); s.add_argument("--set-process", action="append")
    s.add_argument("--status", choices=("untested", "baseline", "tuned")); s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_record)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    s = sub.add_parser("tile", help="print the openscad command for a tile with this ID (quoted; run it as is)")
    s.add_argument("code", nargs="?"); s.add_argument("--printer"); s.add_argument("--layer")
    s.add_argument("--out", default="tile.stl"); s.set_defaults(fn=cmd_tile)
    a = ap.parse_args()
    for k, v in vars(a).items():
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and len(x) > MAX_TEXT_CHARS:
                sys.exit("refused: --%s is %d characters (max %d)" % (k.rstrip("_").replace("_", "-"), len(x), MAX_TEXT_CHARS))
    REG = registry_path(a.registry)
    try:
        if a.cmd in FROM_FIELDS:
            merge_from(a)
        a.fn(a)
    except OSError as e:          # a directory where a file belongs, no permission, disk full: one line, no traceback
        sys.exit("refused: %s: %s" % (safe_text(e.filename if e.filename is not None else REG, 200),
                                      safe_text(e.strerror or e, 120)))


if __name__ == "__main__":
    main()
