"""Shared helpers for the bambu-studio-settings scripts (Python 3.8+, standard library only).

Finds a Bambu Studio install and user folder, loads system presets with their "inherits" chain,
and reads/writes the parts of a Bambu project .3mf that hold print settings.

A .3mf may come from anyone (a download, a model site). Everything read from one is untrusted: read it only
through open_project() (ADR 0006), print it only through safe_text(), and never write names from it to disk
unchecked.
"""
import codecs
import glob
import io
import itertools
import json
import math
import os
import platform
import re
import string
import struct
import sys
import tempfile
import unicodedata
import zipfile
import zlib
from array import array
from xml.parsers import expat
from xml.sax.saxutils import escape

PRESET_TYPES = ("process", "filament", "machine")
XML_ATTR = {'"': "&quot;"}
FLATPAK_ID = "com.bambulab.BambuStudio"

# Limits for reading a project .3mf (a zip). Real Bambu projects are far below these.
MAX_ENTRIES = 10000
MAX_ENTRY_BYTES = 512 * 1024 * 1024       # one uncompressed entry
MAX_TOTAL_BYTES = 1024 * 1024 * 1024      # all entries together, uncompressed
MAX_SETTINGS_BYTES = 32 * 1024 * 1024     # one settings or metadata entry (JSON, ini, XML)
MAX_SETTINGS_TOTAL_BYTES = 64 * 1024 * 1024  # every settings and metadata entry together, from the directory
# The whole-archive budget (ADR 0006 D9): counted across every entry of one archive, refused as soon as passed.
MAX_XML_ELEMENTS = 200000                 # elements of every tree-read (settings and metadata) XML entry
MAX_STRINGS = 1000000                     # XML attribute values and text nodes; JSON strings, keys and scalars;
#                                           ini keys, values and sections; kept G-code-comment lines
MAX_JSON_CONTAINERS = 200000              # JSON objects and arrays, counted before json.loads
MAX_MODEL_OBJECTS = 100000                # <object> in all .model files
MAX_COMPONENTS = 100000                   # stored <component> in all .model files
MAX_BUILD_ITEMS = 10000                   # <item> in the <build> of 3D/3dmodel.model (the only ones stored)
MAX_MODEL_ELEMENTS = 32000000             # elements of all .model files (vertices and triangles included)
MAX_MODEL_OTHER = 1000000                 # elements of all .model files other than <vertex> and <triangle>
MAX_MODEL_CHARS = 16000000                # characters of the ids, names, paths and transforms Geometry keeps
MAX_PROBLEM_LINES = 20                    # verify: PROBLEM or note lines with one template; the rest counted
MAX_CD_BYTES = 16 * 1024 * 1024           # the zip central directory (real ones are a few KB)
MAX_DEPTH = 256                           # nesting of XML elements and JSON arrays/objects
MAX_TAG_BYTES = 64 * 1024                 # XML input expat holds before it turns it into an event (one tag,
#                                           comment, reference ...), checked every XML_CHUNK (ADR 0006)
XML_CHUNK = 64 * 1024                     # bytes fed to expat per Parse call
MAX_XML_NAME_CHARS = 64 * 1024            # characters of distinct element and attribute names in one XML entry
MAX_FINDING_DETAILS = 50                  # post_process findings reported in full; the rest are counted
MAX_RATIO = 200                           # uncompressed / compressed: each entry, the settings and metadata
RATIO_MIN_BYTES = 1024 * 1024             # together and the whole archive, each once it is over this size
# Limits for walking 3MF components (objects built from other objects).
MAX_COMPONENT_DEPTH = 16
MAX_COMPONENT_VISITS = 100000
MAX_VERTICES = 10000000
MAX_TRIANGLES = 20000000                  # Geometry(triangles=True): stored and placed triangles, each

SETTING_KEY_RE = re.compile(r"^[A-Za-z0-9_]+\Z")  # \Z, not $: "key\n" is not a key


class BambuError(Exception):
    """A problem to report to the user as a one-line message (no traceback)."""


class GeometryError(BambuError):
    """An object's geometry cannot be measured safely (cycles, too deep, too big)."""


def run_main(main):
    """Run a script's main() and turn errors into a clean one-line message (BSS_DEBUG=1 shows the traceback)."""
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except BambuError as e:
        sys.exit("error: %s" % safe_text(str(e), 800))
    except Exception as e:  # noqa: BLE001 - a public tool should not dump tracebacks on bad input
        if os.environ.get("BSS_DEBUG"):
            raise
        sys.exit("error: %s: %s (set BSS_DEBUG=1 for details)" % (type(e).__name__, safe_text(str(e), 400)))

# ---------------------------------------------------------------- printing untrusted text

_DROP_CATEGORIES = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")


def safe_text(s, limit=200):
    """One line of printable text from an untrusted value: control and format characters (newlines, escape
    sequences, bidi overrides) become spaces or '?', whitespace runs collapse, and the result is cut at limit."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    out = []
    for ch in s:
        if ch in "\t\n\r\x0b\x0c":
            out.append(" ")
        elif unicodedata.category(ch) in _DROP_CATEGORIES:
            out.append("?")
        else:
            out.append(ch)
    t = re.sub(r"\s+", " ", "".join(out)).strip()
    if limit and len(t) > limit:
        t = t[:max(1, limit - 3)] + "..."
    return t


def safe_tree(v, limit=200):
    """safe_text() applied to every string (keys too) in a JSON-like value."""
    if isinstance(v, dict):
        return dict((safe_text(k, limit), safe_tree(x, limit)) for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return [safe_tree(x, limit) for x in v]
    if isinstance(v, str):
        return safe_text(v, limit)
    return v


def safe_json(v, limit=200):
    return json.dumps(safe_tree(v, limit))


def safe_lines(text, max_lines=25, prefix="  | ", limit=300):
    """The last max_lines lines of untrusted multi-line text (CLI output), each sanitized and prefixed."""
    lines = [safe_text(x, limit) for x in (text or "").splitlines()]
    lines = [x for x in lines if x][-max_lines:]
    return "\n".join(prefix + x for x in lines)

# ---------------------------------------------------------------- install / user folder discovery

def default_install_profiles():
    """Candidate 'resources/profiles' folders of a Bambu Studio install, most likely first."""
    system = platform.system()
    cands = []
    env = os.environ.get("BAMBU_STUDIO_PROFILES")
    if env:
        cands.append(env)
    if system == "Windows":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            if base:
                cands.append(os.path.join(base, "Bambu Studio", "resources", "profiles"))
    elif system == "Darwin":
        cands.append("/Applications/BambuStudio.app/Contents/Resources/profiles")
        cands.append(os.path.expanduser("~/Applications/BambuStudio.app/Contents/Resources/profiles"))
    else:
        share = os.path.join("files", "share", "BambuStudio", "resources", "profiles")
        cands += ["/usr/share/bambu-studio/resources/profiles", "/opt/bambu-studio/resources/profiles",
                  "/app/share/BambuStudio/resources/profiles",
                  os.path.join("/var/lib/flatpak/app", FLATPAK_ID, "current", "active", share),
                  os.path.join(os.path.expanduser("~/.local/share/flatpak/app"), FLATPAK_ID, "current", "active",
                               share)]
    return cands


def find_profiles(explicit=None, required=True):
    """Return the install's profiles folder (the one holding BBL.json and BBL/).
    With required=False, return None instead of exiting when there is none."""
    for c in ([explicit] if explicit else default_install_profiles()):
        if c and os.path.isfile(os.path.join(c, "BBL.json")):
            return c
    if not required:
        return None
    if explicit:
        sys.exit("No BBL.json in %s - pass the install's resources/profiles folder." % explicit)
    sys.exit("Could not find a Bambu Studio install. Pass --profiles <install>/resources/profiles "
             "(or set BAMBU_STUDIO_PROFILES).")


def find_on_path(names, path=None, cwd=None):
    """Like shutil.which, but never looks in the current folder: Windows searches it first, and a downloaded
    folder could hold a fake bambu-studio.exe. Only absolute PATH entries other than the current folder count."""
    path = os.environ.get("PATH", "") if path is None else path
    here = os.path.normcase(os.path.realpath(cwd or os.getcwd()))
    for d in path.split(os.pathsep):
        d = d.strip().strip('"')
        if not d or not os.path.isabs(d) or os.path.normcase(os.path.realpath(d)) == here:
            continue
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p) and (os.name == "nt" or os.access(p, os.X_OK)):
                return p
    return None


def default_exe():
    env = os.environ.get("BAMBU_STUDIO_EXE")
    if env:
        return env
    system = platform.system()
    if system == "Windows":
        p = os.path.join(os.environ.get("ProgramFiles") or r"C:\Program Files", "Bambu Studio", "bambu-studio.exe")
        if os.path.isfile(p):
            return p
    elif system == "Darwin":
        p = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
        if os.path.isfile(p):
            return p
    return find_on_path(["bambu-studio", "BambuStudio", "bambu-studio.exe"])


def default_user_roots():
    """Candidate Bambu Studio data folders (<BambuStudio user folder>), most likely first."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return [os.path.join(appdata, "BambuStudio")]
    if system == "Darwin":
        return [os.path.expanduser("~/Library/Application Support/BambuStudio")]
    return [os.path.expanduser("~/.config/BambuStudio"),
            os.path.join(os.path.expanduser("~/.var/app"), FLATPAK_ID, "config", "BambuStudio")]


def default_user_root():
    """The Bambu Studio data folder (<BambuStudio user folder>), which contains user/<user_id>/:
    the first candidate that exists (Linux: native, then Flatpak), else the first candidate."""
    cands = default_user_roots()
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


def user_dirs(user_root):
    """All user/<user_id> folders under a Bambu Studio data folder."""
    base = os.path.join(user_root, "user")
    if not os.path.isdir(base):
        return []
    return sorted(os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))


def version_dotted(v):
    """'02.09.00.89' -> '2.9.0.89' (the form user presets carry)."""
    try:
        return ".".join(str(int(p)) for p in v.split("."))
    except Exception:
        return v

# ---------------------------------------------------------------- system presets

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class SystemPresets:
    """All presets of one vendor (default BBL) from an install, indexed by name, with inheritance resolved on demand."""

    def __init__(self, profiles_dir, vendor="BBL"):
        self.profiles_dir = profiles_dir
        self.vendor = vendor
        meta = load_json(os.path.join(profiles_dir, vendor + ".json"))
        self.version = meta.get("version", "")
        self.by_name = {}
        for t in PRESET_TYPES:
            for f in glob.glob(os.path.join(profiles_dir, vendor, t, "**", "*.json"), recursive=True):
                try:
                    d = load_json(f)
                except Exception:
                    continue
                if not isinstance(d, dict) or "name" not in d:
                    continue
                d["_type"] = d.get("type", t)
                d["_file"] = f
                self.by_name[d["name"]] = d
        self._cache = {}

    def resolve(self, name, _seen=()):
        """Full settings of a preset: its own keys over its parents' (keys starting with _ are bookkeeping)."""
        if name in self._cache:
            return self._cache[name]
        d = self.by_name.get(name)
        if d is None or name in _seen:
            return None
        parent = d.get("inherits")
        pres = (self.resolve(parent, _seen + (name,)) or {}) if parent else {}
        out = dict(pres)
        out.update(d)  # the child's own keys (name, setting_id, inherits, ...) win
        out["_chain"] = [name] + pres.get("_chain", [])
        self._cache[name] = out
        return out

    def instantiated(self, ptype):
        return sorted(n for n, d in self.by_name.items()
                      if d["_type"] == ptype and str(d.get("instantiation", "")).lower() == "true")

    def compatible(self, ptype, machine_name):
        """Instantiated presets of a type whose (inherited) compatible_printers lists this machine preset."""
        out = []
        for n in self.instantiated(ptype):
            cp = (self.resolve(n) or {}).get("compatible_printers") or []
            if machine_name in cp:
                out.append(n)
        return out


def load_system_presets(explicit=None, vendor="BBL"):
    """SystemPresets of the install, or None when no install is found (for checks that are optional)."""
    prof = find_profiles(explicit, required=bool(explicit))
    return SystemPresets(prof, vendor) if prof else None

# ---------------------------------------------------------------- project .3mf: reading (ADR 0006)
#
# Every script reads a .3mf only through open_project(). It refuses archives whose settings Bambu Studio could
# read differently from these scripts (same name twice, case variants, backslashes, non-ASCII names, settings
# that do not parse), parses every settings and metadata entry, and runs the one post_process detector
# (find_post_process) on all of them. Names are matched the way Bambu Studio matches them: "\" becomes "/" and
# ASCII letters are compared without case.

PROJECT_SETTINGS = "Metadata/project_settings.config"
MODEL_SETTINGS = "Metadata/model_settings.config"
MAIN_MODEL = "3D/3dmodel.model"
# The Prusa importer (Bambu Studio sends PrusaSlicer projects there) loads object settings from this one.
PRUSA_MODEL_SETTINGS = "Metadata/Slic3r_PE_model.config"
# PrusaSlicer's own settings, "; key = value" lines. Bambu Studio does not load it; it is scanned (D10).
PRUSA_CONFIG = "Metadata/Slic3r_PE.config"
# Embedded presets: Bambu Studio loads every entry whose name starts with one of these, whatever its extension.
PRESET_PREFIXES = (("Metadata/print_setting_", "ini"), ("Metadata/process_settings_", "json"),
                   ("Metadata/filament_settings_", "json"), ("Metadata/machine_settings_", "json"))
# Other metadata the detector reads too ("sniff": XML if it starts with "<", JSON if with "{" or "[").
SCANNED_SUFFIXES = ((".config", "sniff"), (".xml", "xml"), (".json", "json"))
NON_ASCII_FOLDERS = ("3d/", "auxiliaries/")  # the only folders where non-ASCII entry names are accepted
PP = "post_process"
UTF8_BOM = b"\xef\xbb\xbf"
SETTING_TAGS = ("metadata", "option")  # XML elements that carry one setting (key= or opt_key=)

_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


class RefusedArchive(BambuError):
    """A .3mf the scripts will not read, because Bambu Studio could read its settings differently from them."""

    def __init__(self, label, reason, resave=True):
        self.reason = reason
        BambuError.__init__(self, "%s: archive refused: %s.%s" % (safe_text(label, 80), reason, (
            " Bambu Studio never writes files like this; if you trust the file, open it in Bambu Studio, save it as "
            "a new project (do not slice it) and use that copy.") if resave else ""))


class BadEntry(BambuError):
    """A settings or XML entry that cannot be parsed safely."""


class Budget(object):
    """The whole-archive budget (ADR 0006 D9). open_project makes one per archive; every tree-read parser and
    Geometry charge it, and passing a cap raises at once (BadEntry, or error=), so no work runs past it.
    used: {kind: count}. The caps are read from the module constants at each charge."""
    KINDS = (("xml_elements", "MAX_XML_ELEMENTS", "XML elements in its settings and metadata"),
             ("strings", "MAX_STRINGS", "strings and values in its settings and metadata"),
             ("json_containers", "MAX_JSON_CONTAINERS", "JSON objects and arrays in its settings and metadata"),
             ("model_objects", "MAX_MODEL_OBJECTS", "objects in its .model files"),
             ("components", "MAX_COMPONENTS", "components in its .model files"),
             ("build_items", "MAX_BUILD_ITEMS", "build items in 3D/3dmodel.model"),
             ("model_elements", "MAX_MODEL_ELEMENTS", "elements in its .model files"),
             ("model_other", "MAX_MODEL_OTHER", "elements other than vertices and triangles in its .model files"),
             ("model_chars", "MAX_MODEL_CHARS", "characters of ids, names, paths and transforms in its .model "
                                                "files"))
    _CAP = dict((k, (c, what)) for k, c, what in KINDS)

    def __init__(self):
        self.used = dict((k, 0) for k, _, _ in self.KINDS)

    def cap(self, kind):
        return globals()[self._CAP[kind][0]]

    def charge(self, kind, n=1, error=None):
        v = self.used[kind] + n
        self.used[kind] = v
        cap = globals()[self._CAP[kind][0]]
        if v > cap:
            raise (error or BadEntry)("the project has more than %d %s" % (cap, self._CAP[kind][1]))


def ascii_lower(s):
    """s with only the ASCII letters lower-cased (the way Bambu Studio compares names and keys)."""
    return s.translate(_ASCII_LOWER)


def norm_name(n):
    """An entry name as Bambu Studio matches it: backslashes become slashes, ASCII letters lower case."""
    return ascii_lower(n.replace("\\", "/"))


def entry_role(norm):
    """How an entry is read, by its normalised name: (role, format, canonical name or prefix).
    role is 'project', 'model', 'preset', 'prusa-model' (Bambu Studio loads settings from these), 'scan' (other
    metadata, checked too) or None; format is 'json', 'ini', 'xml', 'gcode-comment' or 'sniff'."""
    if norm == ascii_lower(PROJECT_SETTINGS):
        return "project", "json", PROJECT_SETTINGS
    if norm == ascii_lower(MODEL_SETTINGS):
        return "model", "xml", MODEL_SETTINGS
    if norm == ascii_lower(PRUSA_MODEL_SETTINGS):
        return "prusa-model", "xml", PRUSA_MODEL_SETTINGS
    if norm == ascii_lower(PRUSA_CONFIG):
        return "scan", "gcode-comment", None
    for prefix, fmt in PRESET_PREFIXES:
        if norm.startswith(ascii_lower(prefix)):
            return "preset", fmt, prefix
    if norm.startswith("metadata/"):
        for suffix, fmt in SCANNED_SUFFIXES:
            if norm.endswith(suffix):
                return "scan", fmt, None
    return None, None, None


def _canonical(name, role, canon):
    """True when a settings entry is spelled exactly the way Bambu Studio writes it."""
    if "\\" in name:
        return False
    if role in ("project", "model", "prusa-model"):
        return name == canon
    if role == "preset":
        return name.startswith(canon)
    return True


def xml_attr(v):
    return escape(str(v), XML_ATTR)


def as_str(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, list):
        return [as_str(x) for x in v]
    return str(v)


def _too_compressed(size, csize):
    return size > RATIO_MIN_BYTES and size > MAX_RATIO * max(1, csize)


def _raw_name(info, name=None):
    """The name to show for a zip entry: the directory name as written. Never ZipInfo.filename, which CPython
    3.12+ replaces with the entry's 0x7075 Unicode-path extra (the attacker's choice, deviation 57)."""
    return name if name is not None else info.orig_filename


def check_zip(z, label="project", records=None):
    """Refuse archives too big to read safely (zip bombs), with RefusedArchive (L3): entry count, entry size,
    total size, and the ratio of each entry and of all entries together (D9: many entries just under the
    per-entry floor cannot add up to more than MAX_RATIO times their compressed size)."""
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES:
        raise RefusedArchive(label, "it has %d entries; the limit is %d" % (len(infos), MAX_ENTRIES))
    total = ctotal = 0
    names = [r.name for r in records] if records is not None and len(records) == len(infos) else [None] * len(infos)
    for it, raw in zip(infos, names):
        if it.file_size > MAX_ENTRY_BYTES:
            raise RefusedArchive(label, "entry %s is %d MB uncompressed; the limit is %d MB" % (
                safe_text(_raw_name(it, raw), 80), it.file_size // 2 ** 20, MAX_ENTRY_BYTES // 2 ** 20))
        if _too_compressed(it.file_size, it.compress_size):
            raise RefusedArchive(label, "entry %s expands %d times (limit %d); this looks like a zip bomb" % (
                safe_text(_raw_name(it, raw), 80), it.file_size // max(1, it.compress_size), MAX_RATIO))
        total += it.file_size
        ctotal += it.compress_size
    if total > MAX_TOTAL_BYTES:
        raise RefusedArchive(label, "it is %d MB uncompressed; the limit is %d MB" % (
            total // 2 ** 20, MAX_TOTAL_BYTES // 2 ** 20))
    if _too_compressed(total, ctotal):
        raise RefusedArchive(label, "its entries together expand %d times (limit %d); this looks like a zip "
                                    "bomb" % (total // max(1, ctotal), MAX_RATIO))


# ---------------------------------------------------------------- the zip container (ADR 0006 D8)
#
# CPython's zipfile and Bambu Studio's miniz find the central directory differently (CPython adds the gap between
# where the directory is and where the end record says it is; miniz uses the offset as written), so one file can
# show each of them different entries. _check_container accepts only the plain layout Bambu Studio writes, where
# both provably read the same end record, the same directory bytes and the same entries.

EOCD_SIG, CD_SIG, LOCAL_SIG, ZIP64_LOCATOR_SIG = b"PK\x05\x06", b"PK\x01\x02", b"PK\x03\x04", b"PK\x06\x07"
ZIP64_EOCD_SIG, ZIP64_EXTRA = b"PK\x06\x06", 0x0001
EOCD_LEN, CD_LEN, LOCAL_LEN, ZIP64_LOCATOR_LEN, ZIP64_EOCD_LEN = 22, 46, 30, 20, 56
U16, U32 = 0xFFFF, 0xFFFFFFFF
REFUSED_FLAGS = 0x0001 | 0x0020 | 0x0040 | 0x2000  # encrypted, patched, strong encryption, masked local header


class ZipRecord(object):
    """One central-directory record as miniz reads it. raw: the name bytes; name: cut at the first NUL (miniz's
    C string) and decoded the way CPython decodes it (UTF-8 with flag bit 11, else cp437); full: all of it."""
    __slots__ = ("raw", "name", "full", "flags", "method", "crc", "csize", "usize", "offset")


def _container_refused(label, check):
    return RefusedArchive(label, "the zip structure is not one Bambu Studio writes (%s)" % check)


def _zip_extras(extra):
    """[(id, data)] of a zip extra field; None when the field is cut short."""
    out, i = [], 0
    while i + 4 <= len(extra):
        tp, ln = struct.unpack("<HH", extra[i:i + 4])
        if i + 4 + ln > len(extra):
            return None
        out.append((tp, extra[i + 4:i + 4 + ln]))
        i += 4 + ln
    return out if i == len(extra) else None


def _zip64_end(f, size, tail, classic, label):
    """(total, cd_size, cd_offset, where the directory must end) from a zip64 end record, or RefusedArchive
    (ADR 0006 Z3, deviation 53). miniz takes every field from the record the locator points to; CPython 3.8 reads
    56 bytes right before the locator, 3.13 the locator's offset. Accepted only when those are one record, with no
    extensible data, on one disk, whose every field is the classic end record's value or its 0xFFFF / 0xFFFFFFFF
    placeholder, so every reader takes the same counts, size and offset."""
    _, loc_disk, rec_off, disks = struct.unpack("<4sIQI", tail[-EOCD_LEN - ZIP64_LOCATOR_LEN:-EOCD_LEN])
    rec_pos = size - EOCD_LEN - ZIP64_LOCATOR_LEN - ZIP64_EOCD_LEN
    if loc_disk or disks != 1:
        raise _container_refused(label, "a multi-disk zip64 locator")
    if rec_pos < 0 or rec_off != rec_pos:
        raise _container_refused(label, "the zip64 locator does not point right before itself")
    f.seek(rec_pos)
    rec = f.read(ZIP64_EOCD_LEN)
    if len(rec) != ZIP64_EOCD_LEN:
        raise _container_refused(label, "the zip64 end record is cut short")
    sig, rec_len, _, _, disk, cd_disk, n_here, total, cd_size, cd_offset = struct.unpack("<4sQHHIIQQQQ", rec)
    if sig != ZIP64_EOCD_SIG or rec_len != ZIP64_EOCD_LEN - 12:
        raise _container_refused(label, "a damaged zip64 end record, or one with extensible data")
    if disk or cd_disk:
        raise _container_refused(label, "a multi-disk archive")
    if n_here != total:
        raise _container_refused(label, "inconsistent entry counts in the zip64 end record")
    for old, new, placeholder in zip(classic, (n_here, total, cd_size, cd_offset), (U16, U16, U32, U32)):
        if old != new and old != placeholder:
            raise _container_refused(label, "the zip64 end record does not agree with the end record")
    return total, cd_size, cd_offset, rec_pos


def _zip64_sizes(extras, usize, csize, offset, n, label):
    """(usize, csize, offset) of directory record n with its zip64 extra field applied, or RefusedArchive
    (deviation 53). Both readers replace exactly the fields that are 0xFFFFFFFF, in this order, from the first
    (miniz) or every (CPython) 0x0001 field; at most one such field, holding exactly those values, keeps them
    reading the same numbers. The values are only numbers here; the caller checks them against the caps."""
    wide = [d for tp, d in extras if tp == ZIP64_EXTRA]
    needed = (usize == U32) + (csize == U32) + (offset == U32)
    if offset == U32 and csize != U32:  # miniz adds the raw offset to csize and cannot open it (deviation 57)
        raise _container_refused(label, "a zip64 offset without a zip64 compressed size in directory record %d" % n)
    if not wide and not needed:
        return usize, csize, offset
    if len(wide) != 1 or len(wide[0]) != 8 * needed:
        raise _container_refused(label, "a zip64 extra field that does not match directory record %d" % n)
    vals = iter(struct.unpack("<%dQ" % needed, wide[0]))
    usize = next(vals) if usize == U32 else usize
    csize = next(vals) if csize == U32 else csize
    offset = next(vals) if offset == U32 else offset
    return usize, csize, offset


def _check_container(f, label):
    """[ZipRecord] of the file's central directory, read the way miniz reads it, or RefusedArchive (BambuError
    for a file that is not a zip at all) unless CPython is bound to read the same (ADR 0006 D8, Z1-Z5 and Z7).
    Reads the last 22 bytes, the directory (at most MAX_CD_BYTES) and 30 bytes per local header."""
    f.seek(0, 2)
    size = f.tell()
    tail_len = min(size, EOCD_LEN + 65535)
    f.seek(size - tail_len)
    tail = f.read(tail_len)
    if EOCD_SIG not in tail:
        raise RefusedArchive(label, "not a .3mf project (not a zip file)", resave=False)
    # Z1: the end record is the last 22 bytes, with no comment, so miniz's backward scan and CPython agree on it
    eocd = tail[-EOCD_LEN:]
    if len(eocd) < EOCD_LEN or eocd[:4] != EOCD_SIG:
        raise _container_refused(label, "the end record is not the last 22 bytes: a zip comment or bytes after it")
    disk, cd_disk, n_here, total, cd_size, cd_offset, comment_len = struct.unpack("<HHHHIIH", eocd[4:])
    if comment_len:
        raise _container_refused(label, "a zip comment")
    # Z2: plain single-disk fields, and a directory size checked before anyone reads it
    if disk or cd_disk:
        raise _container_refused(label, "a multi-disk archive")
    # Z3 (deviation 53): a zip64 locator right before the end record makes miniz and CPython take the counts,
    # size and offset from the zip64 end record; accepted only when it agrees with this one
    cd_end = size - EOCD_LEN
    if size >= EOCD_LEN + ZIP64_LOCATOR_LEN and \
            tail[-EOCD_LEN - ZIP64_LOCATOR_LEN:-EOCD_LEN - ZIP64_LOCATOR_LEN + 4] == ZIP64_LOCATOR_SIG:
        total, cd_size, cd_offset, cd_end = _zip64_end(f, size, tail, (n_here, total, cd_size, cd_offset), label)
    elif n_here != total or total == U16 or cd_size == U32 or cd_offset == U32:
        raise _container_refused(label, "zip64 fields without a zip64 end record, or inconsistent entry counts")
    if cd_size < CD_LEN * total:
        raise _container_refused(label, "the central directory is too small for its entries")
    if cd_size > MAX_CD_BYTES:
        raise _container_refused(label, "the central directory is larger than %d MB" % (MAX_CD_BYTES // 2 ** 20))
    if total > MAX_ENTRIES:  # before the walk (L3: a limit is a refusal)
        raise RefusedArchive(label, "it has %d entries; the limit is %d" % (total, MAX_ENTRIES))
    # Z4: the directory sits right before the end record (or the zip64 one), at the offset written (CPython's
    # offset shift is 0)
    if cd_offset + cd_size != cd_end:
        raise _container_refused(label, "the central directory is not where the end record says it is")
    f.seek(cd_offset)
    cd = f.read(cd_size)
    if len(cd) != cd_size:
        raise _container_refused(label, "the central directory is cut short")
    # Z5: exactly `total` records that use up exactly cd_size bytes (miniz stops after total records, CPython
    # after cd_size bytes); Z3 and Z7 per record
    records, pos = [], 0
    for n in range(total):
        if pos + CD_LEN > cd_size or cd[pos:pos + 4] != CD_SIG:
            raise _container_refused(label, "directory record %d is missing or damaged" % (n + 1))
        (flags, method, crc, csize, usize, nlen, xlen, clen, disk_start, offset) = struct.unpack(
            "<8xHH4xIIIHHHH6xI", cd[pos:pos + CD_LEN])
        end = pos + CD_LEN + nlen + xlen + clen
        if end > cd_size:
            raise _container_refused(label, "directory record %d runs past the directory" % (n + 1))
        raw = cd[pos + CD_LEN:pos + CD_LEN + nlen]
        extras = _zip_extras(cd[pos + CD_LEN + nlen:pos + CD_LEN + nlen + xlen])
        pos = end
        if disk_start == U16 or extras is None:
            raise _container_refused(label, "a zip64 disk number or a damaged extra field in directory record %d"
                                     % (n + 1))
        usize, csize, offset = _zip64_sizes(extras, usize, csize, offset, n + 1, label)
        if usize > MAX_ENTRY_BYTES:  # check_zip says the same later; here before ZipFile reads anything
            raise RefusedArchive(label, "entry %d is %d MB uncompressed; the limit is %d MB" % (
                n + 1, usize // 2 ** 20, MAX_ENTRY_BYTES // 2 ** 20))
        if disk_start:
            raise _container_refused(label, "directory record %d starts on another disk" % (n + 1))
        if method not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise _container_refused(label, "entry %d uses compression method %d (only stored and deflate are read)"
                                     % (n + 1, method))
        if flags & REFUSED_FLAGS:
            raise _container_refused(label, "entry %d is encrypted, patched or masked" % (n + 1))
        if (method == zipfile.ZIP_STORED and csize != usize) or (usize and not csize):
            raise _container_refused(label, "entry %d has sizes that do not fit its method" % (n + 1))
        if offset + LOCAL_LEN + csize > size:
            raise _container_refused(label, "entry %d runs past the end of the file" % (n + 1))
        f.seek(offset)
        if f.read(4) != LOCAL_SIG:
            raise _container_refused(label, "entry %d has no local header at its offset" % (n + 1))
        try:
            enc = "utf-8" if flags & 0x800 else "cp437"
            full, name = raw.decode(enc), raw.split(b"\0", 1)[0].decode(enc)
        except UnicodeDecodeError:
            raise _container_refused(label, "entry %d has a name that is not valid UTF-8" % (n + 1))
        r = ZipRecord()
        r.raw, r.name, r.full, r.flags, r.method = raw, name, full, flags, method
        r.crc, r.csize, r.usize, r.offset = crc, csize, usize, offset
        records.append(r)
    if pos != cd_size:
        raise _container_refused(label, "the central directory has bytes after its %d counted records" % total)
    return records, cd_offset


def _match_records(z, records, label):
    """Z4 and Z6: CPython's ZipFile read the same directory as miniz, the same entries in the same order."""
    infos = z.infolist()
    if len(infos) != len(records):
        raise _container_refused(label, "the end record counts %d entries but the directory holds %d" % (
            len(records), len(infos)))
    for n, (info, r) in enumerate(zip(infos, records)):
        if (info.header_offset, info.compress_size, info.file_size, info.compress_type, info.flag_bits,
                info.CRC) != (r.offset, r.csize, r.usize, r.method, r.flags, r.crc):
            raise _container_refused(label, "entry %d reads differently in two zip readers" % (n + 1))


def _open_zip(path):
    """(file, ZipFile, [ZipRecord]) after the container check (D8) and the size limits. Internal: scripts use
    open_project(). Both readers get the same open file, so they read the same bytes; the caller closes both."""
    if not os.path.isfile(path):
        raise BambuError("No such file: %s" % path)
    label = os.path.basename(path)
    f = open(path, "rb")
    z = None
    try:
        records, cd_offset = _check_container(f, label)
        f.seek(0)
        try:
            z = zipfile.ZipFile(f)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, ValueError, EOFError,
                OSError) as e:
            raise RefusedArchive(label, "not a readable zip file (%s)" % safe_text(str(e), 100))
        if z.start_dir != cd_offset:  # Z4 from CPython's side
            raise _container_refused(label, "the central directory is not where the end record says it is")
        _match_records(z, records, label)
        check_zip(z, label, records)
    except BaseException:
        if z is not None:
            z.close()
        f.close()
        raise
    return f, z, records


READ_CHUNK = 1 << 20


def read_entry(z, info, cap=None, name=None):
    """Bytes of one zip entry, never more than cap (default MAX_ENTRY_BYTES). Takes a ZipInfo only: a name
    could pick the wrong one of two entries with that name. Z8 (exact length, L2): the data is read where both
    zip readers find it (after the local header's name and extra) and refused (RefusedArchive) unless a stored
    entry gives exactly its size, or a deflate stream ends exactly at the directory's compressed size and
    inflates to exactly its uncompressed size (at most size + 1 bytes are ever inflated), with a matching CRC.
    CPython's zipfile stops at the size and ignores a longer stream; miniz fails it. name: the directory name
    to show in messages (default the raw one; never ZipInfo.filename)."""
    if not isinstance(info, zipfile.ZipInfo):
        raise TypeError("read_entry takes a ZipInfo, not a name")
    cap = MAX_ENTRY_BYTES if cap is None else cap
    shown = safe_text(_raw_name(info, name), 80)
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):  # miniz reads nothing else (Z7)
        raise BambuError("entry %s uses compression method %d" % (shown, info.compress_type))
    if info.file_size > cap:
        raise BambuError("entry %s is larger than %d MB." % (shown, cap // 2 ** 20))
    f = z.fp
    if f is None:
        raise BambuError("internal error: the project file is closed")

    def bad(why):
        return RefusedArchive(os.path.basename(str(z.filename or "project")), "entry %s %s" % (shown, why))

    f.seek(info.header_offset)
    head = f.read(LOCAL_LEN)
    if len(head) != LOCAL_LEN or head[:4] != LOCAL_SIG:
        raise bad("has no local header")
    nlen, xlen = struct.unpack("<HH", head[26:30])
    f.seek(info.header_offset + LOCAL_LEN + nlen + xlen)
    # the bytes go into one buffer of exactly the entry's size, which getvalue() hands back without a copy, so
    # the peak is one entry, not its chunks plus their join (B3; deviation 19 measured twice the entry)
    left, size, crc, out = info.compress_size, 0, 0, io.BytesIO()
    if info.file_size:
        out.seek(info.file_size - 1)
        out.write(b"\0")
        out.seek(0)
    if info.compress_type == zipfile.ZIP_STORED:
        while left:
            chunk = f.read(min(READ_CHUNK, left))
            if not chunk:
                raise bad("is cut short")
            left -= len(chunk)
            size += len(chunk)
            if size > info.file_size:
                raise bad("is stored with sizes that differ")
            crc = zlib.crc32(chunk, crc)
            out.write(chunk)
        if size != info.file_size:
            raise bad("is stored with sizes that differ")
    else:
        d, buf, limit = zlib.decompressobj(-15), b"", info.file_size + 1
        while not d.eof:
            if not buf and left:
                buf = f.read(min(READ_CHUNK, left))
                if not buf:
                    raise bad("is cut short")
                left -= len(buf)
            try:
                # at most 4 MB at a time (one 1 MB read can inflate to 200 MB); always >= 1: 0 means no limit
                piece = d.decompress(buf, min(limit - size, 4 * READ_CHUNK))
            except zlib.error:
                raise bad("is not a valid deflate stream")
            buf = d.unconsumed_tail
            if not piece and not buf and not left:
                break  # all input used and nothing more came out: the stream did not end
            size += len(piece)
            if size > info.file_size:
                raise bad("inflates to more bytes than its size says")
            crc = zlib.crc32(piece, crc)
            out.write(piece)
        if not d.eof:
            raise bad("has a deflate stream that does not end within its compressed size")
        if size != info.file_size:
            raise bad("inflates to fewer bytes than its size says")
        if left or buf or d.unused_data:
            raise bad("has bytes after its deflate stream")
    if crc != info.CRC:
        raise bad("fails its CRC check")
    return out.getvalue()



def unsafe_entry_name(n):
    """True for zip entry names that could escape a folder or misbehave when extracted or printed: absolute
    paths, '..', backslashes, drive letters or ':' (Windows streams), control, format (bidi), surrogate,
    private-use, unassigned and line/paragraph separator characters, empty names."""
    if not n or n.startswith("/") or "\\" in n or ":" in n:
        return True
    if any(unicodedata.category(c) in _DROP_CATEGORIES for c in n):
        return True
    return any(p == ".." for p in n.split("/"))


# ---------------------------------------------------------------- settings parsers (JSON, ini, XML)

ASCII_WS = " \t\n\r\x0b\x0c"  # what Bambu Studio trims (boost::trim in the C locale); str.strip() trims more


class Pairs(list):
    """A JSON object as the list of every (key, value) pair, so duplicate keys are all seen."""


def _plain(v):
    """A parsed JSON value with objects as dicts (a repeated key: the last one wins)."""
    if isinstance(v, Pairs):
        return dict((k, _plain(x)) for k, x in v)
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def _check_text_bytes(data, label):
    if b"\x00" in data:
        raise BadEntry("%s has NUL bytes (UTF-16 or binary text is refused)" % label)


def _utf8_text(data, label):
    if data.startswith(UTF8_BOM):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise BadEntry("%s is not UTF-8 text (byte %d)" % (label, e.start))


def _check_utf8(data, label):
    """Refuse bytes that are not strict UTF-8, decoding 1 MB at a time (a mesh can be 512 MB)."""
    dec = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        for i in range(0, len(data), 1 << 20):
            dec.decode(data[i:i + (1 << 20)])
        dec.decode(b"", True)
    except UnicodeDecodeError:
        raise BadEntry("%s is not UTF-8 text" % label)


# A JSON string; an unterminated one runs to the end of the text, so the search never starts again inside it
# (a required closing quote would make '"\"\"\"...' quadratic). Such text is not valid JSON anyway.
# One JSON token: a string (group 1), an opening (2) or closing (3) bracket, or any other scalar (a number, true,
# false or null; no group).
_JSON_TOKEN = re.compile(r'("[^"\\]*(?:\\.[^"\\]*)*"?)|([\[{])|([\]}])|[^\s\[\]{},:"]+', re.S)


def _check_json_depth(text, label, budget=None):
    """Refuse JSON nested deeper than MAX_DEPTH, in one linear pass over its tokens, so the recursive json
    scanner is never driven deep; and charge its strings (keys too), other scalars and containers to the budget
    before json.loads builds any of them (D9). The pass stops as soon as a cap is passed and builds no list of
    the tokens (B1: re.subn kept a list of every piece, about 8 bytes a token)."""
    budget = Budget() if budget is None else budget
    s_left = budget.cap("strings") - budget.used["strings"]
    c_left = budget.cap("json_containers") - budget.used["json_containers"]
    depth = strings = containers = 0
    for m in _JSON_TOKEN.finditer(text):
        g = m.lastindex
        if g == 2:
            depth += 1
            containers += 1
            if depth > MAX_DEPTH:
                raise BadEntry("%s is nested more than %d levels deep" % (label, MAX_DEPTH))
            if containers > c_left:
                break
        elif g == 3:
            depth -= 1
        else:
            strings += 1
            if strings > s_left:
                break
    budget.charge("json_containers", containers)
    budget.charge("strings", strings)


def parse_json_bytes(data, label, budget=None):
    """Every pair of a strict UTF-8 JSON document (Pairs for objects); trailing data and nesting deeper than
    MAX_DEPTH are refused, and so is a document past the budget's string or container caps (a fresh Budget
    when none is given)."""
    _check_text_bytes(data, label)
    text = _utf8_text(data, label)
    _check_json_depth(text, label, budget)
    try:
        return json.loads(text, object_pairs_hook=Pairs)
    except ValueError as e:
        raise BadEntry("%s is not valid JSON (%s)" % (label, safe_text(str(e), 100)))
    except RecursionError:
        raise BadEntry("%s is nested too deeply" % label)


def settings_dict(data, label):
    """A JSON settings object (bytes) as a dict; refused (BadEntry) unless it is a JSON object."""
    v = parse_json_bytes(data, label)
    if not isinstance(v, Pairs):
        raise BadEntry("%s is not a JSON object" % label)
    return _plain(v)


class LineSpans(object):
    """Lines of one entry's text kept as numbers (B1): (line number, start, end, and the start and end of the
    [section] line it is under, or -1) in one array, 40 bytes a line instead of a tuple of new strings; the text
    is sliced again only when the lines are read."""
    __slots__ = ("text", "at")
    WIDTH = 5

    def __init__(self, text):
        self.text, self.at = text, array("q")

    def add(self, n, start, end, sec_start=-1, sec_end=-1):
        self.at.extend((n, start, end, sec_start, sec_end))

    def __len__(self):
        return len(self.at) // self.WIDTH

    def spans(self):
        """(line number, its text, the text of its [section] line or None) of each line kept."""
        at, text = self.at, self.text
        for i in range(0, len(at), self.WIDTH):
            yield at[i], text[at[i + 1]:at[i + 2]], (text[at[i + 3]:at[i + 4]] if at[i + 3] >= 0 else None)


class IniLines(LineSpans):
    """An ini entry as parse_ini reads it. Iterating gives (section, key, value, line) of each pair that mentions
    post_process (B1: no other pair can hold a finding, so no other is kept); sections: a LineSpans of the
    [section] lines that mention it; top: {key: value} of every top-level pair (an embedded preset's settings)."""
    __slots__ = ("sections", "top")

    def __init__(self, text):
        LineSpans.__init__(self, text)
        self.sections = LineSpans(text)
        self.top = {}

    def __iter__(self):
        for n, line, sec in self.spans():
            _, k, v = _ini_line(line.strip(ASCII_WS))
            yield (_ini_line(sec.strip(ASCII_WS))[1] if sec is not None else ""), k, v, n

    def section_names(self):
        """(name, line) of each [section] line that mentions post_process."""
        for n, line, _ in self.sections.spans():
            yield _ini_line(line.strip(ASCII_WS))[1], n


class GcodeCommentLines(LineSpans):
    """Iterating gives (line, key or None, value or text) of each line of a Slic3r_PE.config that mentions
    post_process (parse_gcode_comment)."""
    __slots__ = ()

    def __iter__(self):
        for n, line, _ in self.spans():
            yield (n,) + _gcode_comment_line(line)


# A line that is not blank and not a ';' or '#' comment, once ASCII whitespace is trimmed (F5). The regex skips
# the other lines in C, so a file of blank lines or comments costs no Python work per line (B1).
_INI_CONTENT = re.compile(r"^[ \t\r\x0b\x0c]*([^ \t\n\r\x0b\x0c;#][^\n]*)", re.M)
_PP_ANY = re.compile(PP, re.I | re.A)  # post_process in any ASCII case (re.A: no Unicode case folding)


def _ini_line(s):
    """One trimmed, non-blank ini line read like boost read_ini: (kind, a, b) with kind 'comment' (a: the line),
    'section' (a: its name), 'pair' (a: key, b: value) or 'bad' (a: why)."""
    if s[0] in ";#":
        return "comment", s, None
    if s[0] == "[":
        if not s.endswith("]"):
            return "bad", ": a [section] line without ']'", None
        return "section", s[1:-1].strip(ASCII_WS), None
    if "=" not in s:
        return "bad", " has no '='", None
    k, v = (x.strip(ASCII_WS) for x in s.split("=", 1))
    return "pair", k, v


def _pp_lines(text):
    """(line number, start, end) of each line of text (split at "\\n") that mentions post_process in any ASCII
    case, from one regex scan and counted newlines; no list of every line is built (B1)."""
    n, pos, end = 1, 0, -1
    for m in _PP_ANY.finditer(text):
        i = m.start()
        if i < end:
            continue  # another mention on the same line
        start = text.rfind("\n", 0, i) + 1
        end = text.find("\n", i)
        end = len(text) if end < 0 else end
        n += text.count("\n", pos, start)
        pos = start
        yield n, start, end


def _drop_lines(text, spans):
    """UTF-8 bytes of text without the lines at spans (start, end), in order: the same as joining the other lines
    with "\\n". The kept runs are written as they come, so no list of lines or spans is built (B1)."""
    out, cur, sep = io.BytesIO(), 0, b""

    def block(s):
        out.write(sep)
        out.write(s.encode("utf-8"))
        return b"\n"

    for start, end in spans:
        if start > cur:
            sep = block(text[cur:start - 1])
        cur = end + 1
    if cur <= len(text):
        block(text[cur:])
    return out.getvalue()


def parse_ini(data, label, budget=None):
    """IniLines of an ini entry, read like boost read_ini: lines are trimmed, blank and ';'/'#' lines skipped,
    '[section]' lines start a section, other lines split at the first '='. A line without '=', an empty key or
    a key repeated in its section cannot be read, so it is refused. Every key and section name is kept to find
    repeats, so each pair and each section is charged to the budget as two strings (a fresh Budget when none is
    given); the lines are found by a regex, never split into a list (B1)."""
    _check_text_bytes(data, label)
    text = _utf8_text(data, label)
    charge = (Budget() if budget is None else budget).charge
    out = IniLines(text)
    top, names, keys = out.top, set(), set()  # keys: section + NUL + key, below a [section] (NUL is refused)
    section, sec, n, pos = "", (-1, -1), 1, 0
    for m in _INI_CONTENT.finditer(text):
        n += text.count("\n", pos, m.start())
        pos = m.start()
        kind, a, b = _ini_line(m.group(1).rstrip(ASCII_WS))
        if kind == "comment":
            continue
        if kind == "bad":
            raise BadEntry("%s line %d%s" % (label, n, a))
        charge("strings", 2)
        if kind == "section":
            if a in names:
                raise BadEntry("%s line %d repeats section %s" % (label, n, safe_text(a, 40)))
            names.add(a)
            section, sec = a, m.span(1)
            if _count(a):
                out.sections.add(n, *sec)
            continue
        if not a:
            raise BadEntry("%s line %d has an empty key" % (label, n))
        if section:
            key = section + "\x00" + a
            if key in keys:
                raise BadEntry("%s line %d repeats key %s" % (label, n, safe_text(a, 40)))
            keys.add(key)
        elif a in top:
            raise BadEntry("%s line %d repeats key %s" % (label, n, safe_text(a, 40)))
        else:
            top[a] = b
        if _count(a) or _count(b):
            out.add(n, m.start(1), m.end(1), *sec)
    return out


_GCODE_PAIR = re.compile(r";[ \t]*([A-Za-z0-9_]+)[ \t]*=(.*)\Z", re.S)


def _gcode_comment_line(line):
    """(key, value) of a '; key = value' line of Slic3r_PE.config, else (None, the trimmed line)."""
    if line.endswith("\r"):
        line = line[:-1]
    s = line.strip(ASCII_WS)
    m = _GCODE_PAIR.match(s)
    return (m.group(1), m.group(2).strip(ASCII_WS)) if m else (None, s)


def parse_gcode_comment(data, label, budget=None):
    """GcodeCommentLines of PrusaSlicer's Slic3r_PE.config: the lines that mention post_process (no other line
    can hold a finding, so no other line is kept, B1), each charged to the budget as one string (a fresh Budget
    when none is given). Strict UTF-8, lines split at "\\n" without a trailing "\\r", trimmed of ASCII
    whitespace; "; key = value" is a pair, any other line is text (PrusaSlicer writes a header comment).
    Nothing else is refused but bytes that are not UTF-8 text (D10)."""
    _check_text_bytes(data, label)
    text = _utf8_text(data, label)
    charge = (Budget() if budget is None else budget).charge
    out = GcodeCommentLines(text)
    for n, start, end in _pp_lines(text):
        charge("strings")
        out.add(n, start, end)
    return out


def _local(tag):
    return tag.rsplit(":", 1)[-1]


def walk_xml(data, label, start, end=None, text=None):
    """Parse untrusted XML in one linear pass with expat, calling start(tag, attrs, start_byte),
    end(tag, end_byte) and text(str) in document order (tags without a namespace prefix; end_byte is just past
    the element). Refused (BadEntry): NUL bytes, bytes that are not UTF-8, an XML declaration naming another
    encoding, any DOCTYPE or entity declaration (so no entity expansion), elements nested more than MAX_DEPTH
    levels, more than MAX_TAG_BYTES of input that expat has not turned into an event (which also bounds the
    attributes of one tag), more than MAX_XML_NAME_CHARS characters of distinct element and attribute names, and
    anything not well-formed.

    ADR 0006, expat input invariant: expat is fed XML_CHUNK bytes at a time, and after each piece
    CurrentByteIndex (read outside any handler: just past the last parse event, whether or not a handler ran)
    shows how much input expat still holds. That is one partial token of any kind (a tag, comment, processing
    instruction, reference or junk before the root), so the bound holds whatever the token is: expat never holds
    more than MAX_TAG_BYTES + XML_CHUNK. The names clause covers what expat and pyexpat keep after each event."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    _check_text_bytes(data, label)
    _check_utf8(data, label)  # expat follows a UTF-16 BOM or byte pattern even when told UTF-8
    cap, chunk, name_cap = MAX_TAG_BYTES, XML_CHUNK, MAX_XML_NAME_CHARS
    # pyexpat interns every element and attribute name in this dict, in first-seen order, and expat keeps the
    # same names in its own tables for the whole parse: new names are the dict's last entries.
    names, seen = {}, [0, 0]
    p = expat.ParserCreate("UTF-8", None, names)
    if hasattr(p, "SetReparseDeferralEnabled"):
        # expat 2.6+ may put off parsing a partial token until more input comes; off, every piece is parsed when
        # fed. (Where the switch is missing, expat skips a parse only until the input held since a parse that made
        # no progress has doubled; that is at most MAX_TAG_BYTES or it was refused, and one XML_CHUNK >=
        # MAX_TAG_BYTES doubles it, so the bound still holds. ADR 0006 deviation 47.)
        p.SetReparseDeferralEnabled(False)

    def no_dtd(*args):
        raise BadEntry("%s has a DOCTYPE or entity declaration (refused)" % label)

    def decl(version, encoding, standalone):
        if encoding is not None and encoding.lower() != "utf-8":
            raise BadEntry("%s declares encoding %s (only UTF-8 is read)" % (label, safe_text(encoding, 20)))

    p.XmlDeclHandler = decl
    p.StartDoctypeDeclHandler = no_dtd
    p.EntityDeclHandler = no_dtd
    events, marks = [0], []

    def on_start(tag, attrs):
        events[0] += 1
        n = len(names)
        if n != seen[0]:
            seen[1] += sum(map(len, itertools.islice(reversed(names), n - seen[0])))
            seen[0] = n
            if seen[1] > name_cap:
                raise BadEntry("%s has more than %d characters of distinct element and attribute names" % (
                    label, name_cap))
        if len(marks) >= MAX_DEPTH:
            raise BadEntry("%s is nested more than %d levels deep" % (label, MAX_DEPTH))
        marks.append(events[0])
        start(tag, attrs, p.CurrentByteIndex)

    def on_text(s):
        events[0] += 1
        if text is not None:
            text(s)

    def on_end(tag):
        # expat reports a self-closing element's end just past "/>", and any other element's end at its "</".
        # Self-closing means: nothing happened since its start, and the bytes just before end are "/>".
        mark = marks.pop()
        if end is None:
            return
        i = p.CurrentByteIndex
        if not (mark == events[0] and data[i - 2:i] == b"/>"):
            j = data.find(b">", i)
            i = len(data) if j < 0 else j + 1
        end(tag, i)

    p.StartElementHandler = on_start
    p.EndElementHandler = on_end
    if end is not None:
        p.buffer_text = True
        p.CharacterDataHandler = on_text
    elif text is not None:
        p.buffer_text = True
        p.CharacterDataHandler = text
    view, size, done = memoryview(data), len(data), 0
    try:
        for fed in range(chunk, size + chunk, chunk):
            p.Parse(view[fed - chunk:fed], False)
            done = max(done, p.CurrentByteIndex)  # -1 (no position yet) keeps the last one: never an undercount
            if min(fed, size) - done > cap:
                raise BadEntry("%s has a tag or other markup longer than %d bytes (at byte %d)" % (label, cap, done))
        p.Parse(b"", True)
    except expat.ExpatError as e:
        raise BadEntry("%s is not well-formed XML (%s)" % (label, e))
    finally:
        # The parser and the handlers above refer to each other, and on_end holds data: without this the entry
        # stays alive until the cycle collector runs (a second 512 MB copy in the review's files, measured).
        p = None


OWNER_TAGS = ("object", "part")


class Element(object):
    """One XML element with its byte span [start, end) in the bytes it was parsed from. Set at parse time, so
    no check walks the ancestors (D9): owner, the nearest object or part (this element itself if it is one),
    and in_setting, true inside a <metadata>/<option> whose key names post_process."""
    __slots__ = ("tag", "raw_tag", "attrs", "start", "end", "children", "parent", "texts", "owner", "in_setting")

    def __init__(self, raw_tag, attrs, start, parent):
        self.raw_tag, self.tag, self.attrs, self.start, self.end = raw_tag, _local(raw_tag), attrs, start, start
        self.children, self.parent, self.texts = [], parent, []
        up = parent.owner if parent is not None else None
        self.owner = self if self.tag in OWNER_TAGS else up
        self.in_setting = (parent is not None and parent.in_setting) or _setting_element(self)

    @property
    def text(self):
        return "".join(self.texts)

    def iter(self, tag=None):
        """This element and every descendant, in document order (no recursion)."""
        stack = [self]
        while stack:
            e = stack.pop()
            if tag is None or e.tag == tag:
                yield e
            stack.extend(reversed(e.children))

    def meta(self):
        """{key: value} of the direct <metadata key=... value=...> children; a repeated key: the last one wins,
        as in Bambu Studio."""
        return dict((c.attrs["key"], c.attrs.get("value", "")) for c in self.children
                    if c.tag == "metadata" and "key" in c.attrs)


def xml_tree(data, label, budget=None):
    """The parsed document (a '#document' Element holding the root element). Each element and each attribute
    value and text node it keeps is charged to the budget (a fresh Budget when none is given), so the trees of
    one archive hold at most MAX_XML_ELEMENTS elements and MAX_STRINGS strings together (D9)."""
    budget = Budget() if budget is None else budget
    doc = Element("#document", {}, 0, None)
    stack = [doc]
    charge = budget.charge

    def start(tag, attrs, i):
        charge("xml_elements")
        if attrs:
            charge("strings", len(attrs))
        e = Element(tag, attrs, i, stack[-1])
        stack[-1].children.append(e)
        stack.append(e)

    def end(tag, i):
        stack.pop().end = i

    def text(s):
        charge("strings")
        stack[-1].texts.append(s)

    walk_xml(data, label, start, end, text)
    doc.end = len(data) if isinstance(data, bytes) else len(data.encode("utf-8"))
    return doc


def apply_edits(data, edits):
    """data with each (start, end, new_bytes) applied; edits must not overlap."""
    out, cur = [], 0
    for s, e, new in sorted(edits, key=lambda t: (t[0], t[1])):
        if s < cur:
            raise BambuError("internal error: overlapping edits")
        out.append(data[cur:s])
        out.append(new)
        cur = e
    out.append(data[cur:])
    return b"".join(out)


def line_span(data, e):
    """(start, end) of an element's bytes plus the indentation and line break just before it."""
    s = e.start
    while s > 0 and data[s - 1:s] in (b" ", b"\t"):
        s -= 1
    if s > 0 and data[s - 1:s] == b"\n":
        s -= 1
        if s > 0 and data[s - 1:s] == b"\r":
            s -= 1
    return s, e.end


def drop_elements(data, elems):
    """data without the given elements (and their indentation); an element inside another one goes with it."""
    spans, last = [], -1
    for e in sorted(elems, key=lambda x: (x.start, -x.end)):
        if e.end <= last:
            continue
        spans.append(line_span(data, e) + (b"",))
        last = e.end
    return apply_edits(data, spans)


# ---------------------------------------------------------------- the one post_process detector

class Parsed(object):
    """One settings or metadata entry: its bytes and what its parser read (fmt json, ini, xml, gcode-comment,
    raw or empty). value is None once open_project's detector has run (D9), except for the model settings."""
    __slots__ = ("name", "norm", "fmt", "data", "value")

    def __init__(self, name, norm, fmt, data, value):
        self.name, self.norm, self.fmt, self.data, self.value = name, norm, fmt, data, value


class Finding(object):
    """A post_process setting (or text) found in an entry. strippable: strip_post_process can remove it exactly.
    where: the place in the entry, such as "(key a > post_process)", or "" (never a leading space); value is
    sanitized (safe_text, 200 characters)."""
    __slots__ = ("entry", "norm", "where", "value", "fmt", "strippable", "on_object")

    def __init__(self, entry, norm, where, value, fmt, strippable, on_object=False):
        self.entry, self.norm, self.where, self.value = entry, norm, where, value
        self.fmt, self.strippable, self.on_object = fmt, strippable, on_object

    def as_dict(self):
        return {"entry": self.entry, "where": self.where, "value": self.value}

    def place(self):
        """The entry name and the place in it, joined with one space, sanitized for printing."""
        return safe_text(self.entry, 80) + ((" " + safe_text(self.where, 120)) if self.where else "")


class FindingSet(object):
    """The findings of one archive or file (D9): the first MAX_FINDING_DETAILS in full (details), the rest only
    counted (more), and per entry (norm) whether every finding in it can be removed exactly (strippable)."""

    def __init__(self, limit=None):
        self.limit = MAX_FINDING_DETAILS if limit is None else limit
        self.details, self.more, self.strippable = [], 0, {}

    def add(self, p, strippable, where, value, on_object=False):
        """where and value are text, or functions giving it: they are only called for detailed findings."""
        self.strippable[p.norm] = self.strippable.get(p.norm, True) and bool(strippable)
        if len(self.details) >= self.limit:
            self.more += 1
            return
        where = where() if callable(where) else where
        value = value() if callable(value) else value
        self.details.append(Finding(p.name, p.norm, where, safe_text(value, 200), p.fmt, bool(strippable),
                                    on_object))

    def __len__(self):
        return len(self.details) + self.more


def _parse_value(data, name, fmt, budget=None):
    if fmt == "json":
        return parse_json_bytes(data, name, budget)
    if fmt == "ini":
        return parse_ini(data, name, budget)
    if fmt == "xml":
        return xml_tree(data, name, budget)
    if fmt == "gcode-comment":
        return parse_gcode_comment(data, name, budget)
    return None


def parse_settings_bytes(data, name, fmt, norm=None, budget=None):
    """Parse one settings or metadata entry (D3). Raises BadEntry when it cannot be read safely (or when it
    passes the budget's caps; a fresh Budget when none is given)."""
    norm = norm_name(name) if norm is None else norm
    if not data:
        return Parsed(name, norm, "empty", data, None)  # Bambu Studio skips empty entries too
    _check_text_bytes(data, name)
    if fmt == "sniff":
        head = (data[3:] if data.startswith(UTF8_BOM) else data).lstrip()[:1]
        fmt = "xml" if head == b"<" else "json" if head in (b"{", b"[") else "raw"
    return Parsed(name, norm, fmt, data, _parse_value(data, name, fmt, budget))


def _tree(p):
    """The parsed value of an entry, parsed again when the detector freed it (D9)."""
    return p.value if p.value is not None else _parse_value(p.data, p.name, p.fmt)


def _is_pp(key):
    return isinstance(key, str) and ascii_lower(key) == PP


def _empty(v):
    """True for a value that is no command: None, ASCII whitespace only (F5: U+3000 or U+00A0 alone is a value),
    an empty object, or a list of empty values."""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip(ASCII_WS)
    if isinstance(v, Pairs):
        return not v
    if isinstance(v, list):
        return all(_empty(x) for x in v)
    return False


def _value_text(v):
    if isinstance(v, str):
        return v
    if isinstance(v, list) and not isinstance(v, Pairs) and all(isinstance(x, str) for x in v):
        return "; ".join(x for x in v if x.strip(ASCII_WS))
    return json.dumps(_plain(v))


def _count(s):
    return ascii_lower(s).count(PP) if isinstance(s, str) else 0


def _json_where(node):
    """'(key a > b > post_process)' from a parent-linked path node (key, parent): the last 4 keys at most, and
    '' for the plain top-level key."""
    keys, n = [], node
    while n is not None and len(keys) < 5:
        keys.append(n[0])
        n = n[1]
    if len(keys) == 1 and keys[0] == PP:
        return ""
    cut = len(keys) > 4
    keys = list(reversed(keys[:4]))
    return "(key %s%s)" % ("... > " if cut else "", " > ".join(safe_text(k, 40) for k in keys))


def _json_findings(p, fs):
    seen = 0
    stack = [(None, p.value)]  # (path node, value); a path node is (key, parent node)
    while stack:
        node, v = stack.pop()
        if isinstance(v, Pairs):
            for k, x in v:
                seen += _count(k)
                child = (k, node)
                if _is_pp(k) and not _empty(x):
                    fs.add(p, True, lambda c=child: _json_where(c), lambda x=x: _value_text(x))
                stack.append((child, x))
        elif isinstance(v, list):
            stack.extend((node, x) for x in v)
        else:
            seen += _count(v)
    return seen


def _ini_findings(p, fs):
    """F4: a post_process key (any section) with a value is removed with its line; post_process in a section
    name, in another key's name or in another key's value cannot be removed exactly (Bambu Studio reads top-level
    keys only, and dropping a header would move its keys to the top level)."""
    seen = 0
    for name, n in p.value.section_names():
        c = _count(name)
        seen += c
        if c:
            fs.add(p, False, "(line %d, section [%s])" % (n, safe_text(name, 40)), name)
    for section, k, v, n in p.value:
        c = _count(k) + _count(v)
        seen += c
        if _is_pp(k):
            if not _empty(v):
                fs.add(p, True, "(line %d)" % n, v)
        elif c:
            fs.add(p, False, "(line %d, key %s)" % (n, safe_text(k, 40)), "%s = %s" % (k, v))
    return seen


def _gcode_comment_findings(p, fs):
    """D10: '; post_process = <value>' with a value is a finding removed with its line; the empty value
    PrusaSlicer always writes is not one. post_process anywhere else cannot be removed exactly."""
    seen = 0
    for n, k, v in p.value:
        c = _count(k) + _count(v)
        seen += c
        if k is not None and _is_pp(k):
            if not _empty(v):
                fs.add(p, True, "(line %d)" % n, v)
        elif c:
            fs.add(p, False, "(line %d)" % n, v if k is None else "%s = %s" % (k, v))
    return seen


def _setting_element(e):
    return e.tag in SETTING_TAGS and (PP in ascii_lower(e.attrs.get("key", "")) or
                                      PP in ascii_lower(e.attrs.get("opt_key", "")))


def _describe(e):
    """'(<tag key=k> in part 3 of object 1)': the element and at most two owners, no ancestor walk (D9)."""
    key = e.attrs.get("key", e.attrs.get("opt_key"))
    where = "(<%s%s>" % (e.tag, (" key=%s" % safe_text(key, 60)) if key is not None else "")
    a = e.owner
    if a is not None:
        where += " in %s %s" % (a.tag, safe_text(a.attrs.get("id", "?"), 20))
        b = a.parent.owner if a.parent is not None else None
        if b is not None:
            where += " of %s %s" % (b.tag, safe_text(b.attrs.get("id", "?"), 20))
    return where + ")"


def _xml_value(e, tokens):
    if _setting_element(e):
        return e.attrs.get("value", e.text)
    return next(t for t in tokens if PP in ascii_lower(t))


def _xml_findings(p, fs):
    seen = 0
    data = p.data
    for e in p.value.iter():
        if e is p.value:
            continue
        closing = 1 if data[e.end - 2:e.end] == b"/>" else 2  # the tag name appears again in an end tag
        tokens = [e.raw_tag] * closing + [x for kv in e.attrs.items() for x in kv] + [e.text]
        c = sum(_count(t) for t in tokens)
        if not c:
            continue
        seen += c
        fs.add(p, e.in_setting, lambda e=e: _describe(e), lambda e=e, t=tokens: _xml_value(e, t),
               on_object=e.owner is not None)
    return seen


def entry_findings(p, fs=None):
    """Add the findings in one parsed entry to fs (a FindingSet; a new one when None) and return fs: its
    parser's, plus the belt: more 'post_process' (ASCII, any case) in the raw bytes than the parser reported (in
    a comment, say) is a finding too, so a parser gap is not silent."""
    fs = FindingSet() if fs is None else fs
    seen = 0
    if p.fmt == "json":
        seen = _json_findings(p, fs)
    elif p.fmt == "ini":
        seen = _ini_findings(p, fs)
    elif p.fmt == "xml":
        seen = _xml_findings(p, fs)
    elif p.fmt == "gcode-comment":
        seen = _gcode_comment_findings(p, fs)
    if p.data.lower().count(PP.encode("ascii")) > seen:
        fs.add(p, p.fmt == "ini", "(text the parser does not read, such as a comment)", "")
    return fs


def find_post_process(index):
    """A FindingSet of every post_process finding in a project: project settings, embedded presets, model
    settings, the Prusa importer's entries and the other metadata entries. open_project runs the detector on
    each entry as it reads it (D9); this runs it again, parsing again the entries whose trees were freed."""
    fs = FindingSet()
    for p in index.parsed.values():
        if p.value is None and p.fmt in ("json", "ini", "xml", "gcode-comment"):
            p = Parsed(p.name, p.norm, p.fmt, p.data, _tree(p))
        entry_findings(p, fs)
    return fs


def _strip_pairs(v):
    if isinstance(v, Pairs):
        return dict((k, [] if _is_pp(k) else _strip_pairs(x)) for k, x in v)
    if isinstance(v, list):
        return [_strip_pairs(x) for x in v]
    return v


def _cannot_strip(p, why):
    return BambuError("cannot remove post_process from %s exactly (found in %s); nothing written" % (
        safe_text(p.name, 80), why))


def _strip_ini(p):
    """F4: drop the lines whose key is post_process (in any section) and the comment lines that mention it;
    no other line is dropped or moved. Only a line that mentions post_process is dropped or refused, so only
    those lines are read (B1)."""
    text = _utf8_text(p.data, p.name)

    def drop():
        for n, start, end in _pp_lines(text):
            kind, a, b = _ini_line(text[start:end].strip(ASCII_WS))
            if (kind == "pair" and _is_pp(a)) or kind == "comment":
                yield start, end
            elif kind in ("section", "pair"):
                raise _cannot_strip(p, "line %d" % n)
    return _drop_lines(text, drop())


def _strip_gcode_comment(p):
    """D10: drop the '; post_process = ...' lines; post_process anywhere else cannot be removed exactly."""
    text = _utf8_text(p.data, p.name)

    def drop():
        for n, start, end in _pp_lines(text):
            k, v = _gcode_comment_line(text[start:end])
            if k is None or not _is_pp(k):
                raise _cannot_strip(p, "line %d" % n)
            yield start, end
    return _drop_lines(text, drop())


def strip_parsed(p):
    """New bytes for a parsed entry without its post_process: JSON dumped again with every post_process key set
    to [] (repeated keys collapse), ini and Slic3r_PE.config without the post_process lines (F4, D10), XML
    without the <metadata>/<option> elements whose key names it. An entry whose tree was freed is parsed again."""
    if p.fmt == "json":
        return (json.dumps(_strip_pairs(_tree(p)), indent=4) + "\n").encode("utf-8")
    if p.fmt == "ini":
        return _strip_ini(p)
    if p.fmt == "gcode-comment":
        return _strip_gcode_comment(p)
    if p.fmt == "xml":
        return drop_elements(p.data, [e for e in _tree(p).iter() if _setting_element(e)])
    raise BambuError("cannot remove post_process from %s" % safe_text(p.name, 80))


def strip_post_process(index):
    """{norm: new bytes} for every entry with a finding, from the per-entry flags (never the detail list, which
    stops at MAX_FINDING_DETAILS). Raises BambuError when a finding cannot be removed exactly (post_process in an
    object name, an attribute or a comment): such a file is not written or sliced."""
    bad = sorted(n for n, ok in index.strippable.items() if not ok)
    if bad:
        f = next((f for f in index.findings if f.norm == bad[0] and not f.strippable), None)
        raise BambuError("cannot remove post_process from %s exactly (found in %s); nothing written. Do not "
                         "slice this file unless you trust its author." % (
                             safe_text(index.parsed[bad[0]].name, 80),
                             safe_text(f.where.strip(" ()"), 120) if f is not None and f.where else "the file"))
    return dict((norm, strip_parsed(index.parsed[norm])) for norm in sorted(index.strippable))


def check_settings_file(data, label):
    """(Parsed, FindingSet) of a preset JSON file given outside a project (refused unless a JSON object)."""
    p = parse_settings_bytes(data, label, "json", norm=label)
    if not isinstance(p.value, Pairs):
        raise BadEntry("%s is not a preset (not a JSON object)" % label)
    return p, entry_findings(p)


# Per-layer custom G-code (B2): Bambu Studio's own file and the one its Prusa importer reads (names folded).
LAYER_GCODE_ENTRIES = ("metadata/custom_gcode_per_layer.xml", "metadata/prusa_slicer_custom_gcode_per_print_z.xml")
LAYER_GCODE_SHOWN = 3


def layer_gcode(tree):
    """(count, [(z, text)] of the first LAYER_GCODE_SHOWN) of the per-layer custom G-code in a parsed
    custom_gcode_per_layer.xml: each <layer> (Prusa: <code>) with text that Bambu Studio inserts at that height,
    its extra attribute (or gcode, in files written without a type attribute). Pause messages count too."""
    count, first = 0, []
    for e in tree.iter():
        if e.tag not in ("layer", "code"):
            continue
        a = e.attrs
        text = a.get("extra", "") if "type" in a else a.get("gcode", "")
        if not text.strip(ASCII_WS):
            continue
        count += 1
        if len(first) < LAYER_GCODE_SHOWN:
            first.append((a.get("top_z", a.get("print_z", "?")), text))
    return count, first


# ---------------------------------------------------------------- the one reader

class ProjectIndex(object):
    """A project .3mf read by open_project(). entries: {norm: ZipInfo}; infos: [(ZipInfo, name, norm, unsafe)]
    in archive order (name: the central-directory name miniz matches on, D8 Z6); parsed: {norm: Parsed} for
    settings and metadata (bytes and format; the trees are freed, D9); project_settings: dict or None; presets:
    [(ZipInfo, kind, dict, raw name)]; model_settings: parsed Element or None (model_settings_bytes: its bytes); models:
    {norm: ZipInfo} of the .model files; unsafe: entry names left out on write; findings: the first
    MAX_FINDING_DETAILS post_process findings, more_findings: how many more, strippable: {norm: bool} for every
    entry with a finding."""

    def __init__(self, path, z, f=None):
        self.path, self.label, self.zip, self._file = path, os.path.basename(path), z, f
        self.entries, self.infos, self.parsed, self.models, self.unsafe = {}, [], {}, {}, []
        self.project_settings, self.presets, self.model_settings, self.model_settings_bytes = None, [], None, None
        self.findings, self.more_findings, self.strippable = [], 0, {}
        self.layer_gcode = []  # [(entry name, count, [(z, text)] of the first few)]: per-layer custom G-code (B2)
        self.budget = Budget()
        self.names = {}  # ZipInfo -> its raw directory name (what messages show, never ZipInfo.filename)

    def geometry(self, triangles=False, max_triangles=None):
        """A Geometry of every .model file, charged to this archive's budget (D9)."""
        geo = Geometry(triangles=triangles, max_triangles=max_triangles, budget=self.budget)
        for n, info in self.models.items():
            geo.add(n, self.read(info))
        return geo

    def read(self, info, cap=None):
        return read_entry(self.zip, info, cap, self.names.get(info))

    def close(self):
        self.zip.close()
        if self._file is not None:
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _index(idx, records):
    label, keys = idx.label, {}
    for info, r in zip(idx.zip.infolist(), records):
        name, full = r.name, r.full  # the directory name as miniz sees it, cut at NUL (B14, Z6)
        norm = norm_name(name)
        key = norm.casefold()
        if key in keys:
            raise RefusedArchive(label, "two entries have the same name when case and slashes are ignored "
                                        "(%s and %s)" % (safe_text(keys[key], 80), safe_text(name, 80)))
        keys[key] = name
        if any(ord(c) > 127 for c in full) and not norm.startswith(NON_ASCII_FOLDERS):
            raise RefusedArchive(label, "entry name %s has non-ASCII characters outside 3D/ and Auxiliaries/"
                                 % safe_text(full, 80))
        unsafe = unsafe_entry_name(full)
        if unsafe:
            idx.unsafe.append(name)
        idx.infos.append((info, name, norm, unsafe))
        idx.names[info] = name
        idx.entries[norm] = info
        if norm.endswith(".model"):
            idx.models[norm] = info
    settings = [info for info, _, norm, _ in idx.infos if entry_role(norm)[0] is not None]
    total = sum(info.file_size for info in settings)
    if total > MAX_SETTINGS_TOTAL_BYTES:  # D9: from the directory, before any entry is read
        raise RefusedArchive(label, "its settings and metadata entries add up to %d MB uncompressed; the limit is "
                                    "%d MB" % (total // 2 ** 20, MAX_SETTINGS_TOTAL_BYTES // 2 ** 20))
    ctotal = sum(info.compress_size for info in settings)
    if _too_compressed(total, ctotal):  # D9: one ratio for all of them, so no entry hides under the 1 MB floor
        raise RefusedArchive(label, "its settings and metadata entries together expand %d times (limit %d); this "
                                    "looks like a zip bomb" % (total // max(1, ctotal), MAX_RATIO))
    fs = FindingSet()
    for info, name, norm, _ in idx.infos:
        role, fmt, canon = entry_role(norm)
        if role is None:
            continue
        shown = safe_text(name, 80)
        if "\\" in name:  # L1: its own reason, for every role (scan roles have no canonical name to show)
            raise RefusedArchive(label, "settings or metadata entry %s has a backslash in its name (Bambu Studio "
                                        "writes /)" % shown)
        if not _canonical(name, role, canon):
            raise RefusedArchive(label, "settings entry %s is not spelled the way Bambu Studio writes it (%s)" % (
                shown, canon + ("..." if role == "preset" else "")))
        if info.file_size > MAX_SETTINGS_BYTES:
            raise RefusedArchive(label, "%s is larger than %d MB" % (shown, MAX_SETTINGS_BYTES // 2 ** 20))
        try:
            data = read_entry(idx.zip, info, MAX_SETTINGS_BYTES, name)
            p = parse_settings_bytes(data, shown, fmt, norm, idx.budget)
        except RefusedArchive:
            raise
        except BadEntry as e:
            raise RefusedArchive(label, str(e))
        except BambuError as e:
            raise RefusedArchive(label, "%s: %s" % (shown, e))
        except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError, ValueError) as e:
            raise RefusedArchive(label, "%s cannot be read (%s)" % (shown, safe_text(str(e), 100)))
        p.name = name
        idx.parsed[norm] = p
        if role in ("project", "preset") and p.fmt == "json" and not isinstance(p.value, Pairs):
            raise RefusedArchive(label, "%s is not a JSON object" % shown)
        if role == "project" and p.fmt == "json":
            idx.project_settings = _plain(p.value)
        elif role == "model" and p.fmt == "xml":
            idx.model_settings, idx.model_settings_bytes = p.value, data
        elif role == "preset" and p.fmt != "empty":
            d = _plain(p.value) if p.fmt == "json" else p.value.top
            idx.presets.append((info, canon[len("Metadata/"):-1], d, name))
        entry_findings(p, fs)  # D9: detect now, then drop the tree before the next entry is read
        if norm in LAYER_GCODE_ENTRIES and p.fmt == "xml":
            count, first = layer_gcode(p.value)
            if count:
                idx.layer_gcode.append((name, count, first))
        if role != "model":
            p.value = None
    idx.findings, idx.more_findings, idx.strippable = fs.details, fs.more, fs.strippable



def open_project(path):
    """Read a project .3mf the one safe way (ADR 0006): a ProjectIndex, or RefusedArchive / BambuError."""
    f, z, records = _open_zip(path)
    idx = ProjectIndex(path, z, f)
    try:
        _index(idx, records)
    except BaseException:
        idx.close()
        raise
    return idx


FILE_MODE = 0o100644 << 16               # a regular file, rw-r--r-- (F6)
DIR_MODE = (0o40755 << 16) | 0x10        # a folder, rwxr-xr-x, plus the MS-DOS folder bit


def write_project(index, out_path, replace=None, drop=None):
    """Copy a project to out_path, replacing some entries ({norm: str or bytes}) and leaving out entries for which
    drop(norm) is true. Entries with unsafe names are always left out. Each entry is written under its own name
    with a fixed mode (F6): no attribute, extra field or comment from the input reaches the output.
    Returns (unsafe_names_left_out, dropped_names)."""
    replace = replace or {}
    norms = [n for _, _, n, _ in index.infos]
    if len(set(norms)) != len(norms):
        raise BambuError("internal error: an entry name repeats")  # open_project refuses these
    missing = sorted(set(replace) - set(norms))
    if missing:
        raise BambuError("internal error: no entry %s to replace" % safe_text(missing[0], 80))
    unsafe, dropped = [], []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, name, norm, bad in index.infos:
            if bad:
                unsafe.append(name)
                continue
            if drop is not None and drop(norm):
                dropped.append(name)
                continue
            data = replace[norm] if norm in replace else read_entry(index.zip, info, None, name)
            if isinstance(data, str):
                data = data.encode("utf-8")
            zi = zipfile.ZipInfo(name, max(info.date_time, (1980, 1, 1, 0, 0, 0)))
            zi.compress_type = info.compress_type if info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) \
                else zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = DIR_MODE if name.endswith("/") else FILE_MODE
            zout.writestr(zi, data)
    return unsafe, dropped


def check_written(path):
    """The post-write check: a file the scripts wrote must read back through open_project with no
    post_process finding. Raises BambuError otherwise (callers run it inside atomic_write, which then deletes
    the file)."""
    with open_project(path) as idx:
        left = idx.findings
    if left or idx.strippable:
        raise BambuError("internal check failed: the output still has post_process in %s; nothing written" %
                         ", ".join(sorted(set(safe_text(f.entry, 80) for f in left))))


def same_file(a, b):
    """True when two paths name the same file (symlink, hard link, or a case-insensitive alias)."""
    try:
        if os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def atomic_write(dst, writer, overwrite=False):
    """Call writer(tmp_path) on a temp file in dst's folder, then move it onto dst in one step.
    Refuses (BambuError) when dst exists and overwrite is false. The temp file is removed on any failure."""
    folder = os.path.dirname(os.path.abspath(dst))
    if not os.path.isdir(folder):
        raise BambuError("Output folder does not exist: %s" % folder)
    fd, tmp = tempfile.mkstemp(prefix=".bss_", suffix=".tmp", dir=folder)
    os.close(fd)
    try:
        result = writer(tmp)
        if os.name != "nt":
            mask = os.umask(0)
            os.umask(mask)
            os.chmod(tmp, 0o666 & ~mask)
        if os.path.lexists(dst) and not overwrite:
            raise BambuError("%s already exists; not replacing it (pass --overwrite to replace it)." % dst)
        os.replace(tmp, dst)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return result


def post_process_scripts(settings):
    """Non-empty post-processing commands in a settings dict given outside a project (a preset file, user input):
    any top-level key that is post_process ignoring ASCII case. Bambu Studio runs them on this computer after
    slicing. For a .3mf use find_post_process (open_project's findings)."""
    if not isinstance(settings, dict):
        return []
    out = []
    for k, v in settings.items():
        if not _is_pp(k) or v is None:
            continue
        out += [str(x) for x in (v if isinstance(v, list) else [v]) if str(x).strip()]
    return out


META_SKIP = {"name", "extruder", "matrix", "source_file", "source_object_id", "source_volume_id",
             "source_offset_x", "source_offset_y", "source_offset_z", "face_count"}
ID_RE = re.compile(r"^[0-9]{1,9}\Z")  # B4: ASCII digits, short enough for int() to be cheap; \Z, not $
MAX_INT_CHARS = 18                     # B4: longer digit strings are not read (int() is quadratic on 3.8-3.10)


def model_objects(tree):
    """The <object id=N> elements of a parsed model_settings.config."""
    return [e for e in tree.iter("object") if ID_RE.match(e.attrs.get("id", ""))] if tree is not None else []


def parse_objects(tree):
    """Objects in a parsed model_settings.config: id, name, extruder, settings (object-level overrides), parts."""
    objs = []
    for o in model_objects(tree):
        meta = o.meta()
        parts = []
        for pt in o.children:
            if pt.tag != "part":
                continue
            pmeta = pt.meta()
            parts.append({"id": pt.attrs.get("id"), "subtype": pt.attrs.get("subtype") or "normal_part",
                          "name": pmeta.get("name", ""), "extruder": pmeta.get("extruder"),
                          "settings": {k: v for k, v in pmeta.items() if k not in META_SKIP}})
        objs.append({"id": o.attrs["id"], "name": meta.get("name", ""), "extruder": meta.get("extruder"),
                     "settings": {k: v for k, v in meta.items() if k not in META_SKIP}, "parts": parts})
    return objs


def parse_plates(tree):
    """{object_id: plate_index (1-based)} plus per-plate metadata from the <plate> elements."""
    plate_of, plates = {}, {}
    for pl in (tree.iter("plate") if tree is not None else []):
        meta = pl.meta()
        idx = to_int(meta["plater_id"], len(plates) + 1) if ID_RE.match(meta.get("plater_id", "")) \
            else len(plates) + 1
        plates[idx] = meta
        for m in pl.iter("metadata"):
            if m.attrs.get("key") == "object_id" and ID_RE.match(m.attrs.get("value", "")):
                plate_of[m.attrs["value"]] = idx
    return plate_of, plates


def to_int(v, default):
    """int(v), or default when v is not an integer of at most MAX_INT_CHARS characters (B4)."""
    s = str(v).strip()
    if len(s) > MAX_INT_CHARS:
        return default
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def object_filaments(obj):
    """1-based filament numbers an object prints with (object extruder plus any part extruders)."""
    fs = set()
    base = to_int(obj.get("extruder") or "1", 1)
    printing_parts = [p for p in obj["parts"] if p["subtype"] in ("normal_part", "")]
    if not printing_parts:
        fs.add(base)
    for p in printing_parts:
        fs.add(to_int(p.get("extruder") or base, base))
    return fs


def effective(obj, ps, key, default=None):
    """An object's value for a setting: object override, else the plate (project) value."""
    if key in obj["settings"]:
        return obj["settings"][key]
    v = ps.get(key, default)
    if isinstance(v, list):
        v = v[0] if v else default
    return v


# Build plates: curr_bed_type names as Bambu Studio writes them, and the filament key holding that plate's bed
# temperature (each with an _initial_layer twin for the first layer). Keys are in assets/known_setting_keys.txt.
BED_TYPES = (("Cool Plate", "cool_plate_temp"), ("Engineering Plate", "eng_plate_temp"),
             ("High Temp Plate", "hot_plate_temp"), ("Textured PEI Plate", "textured_plate_temp"),
             ("Supertack Plate", "supertack_plate_temp"))
CLI_DEFAULT_BED = "Cool Plate"   # what the Bambu Studio CLI uses when a project has no curr_bed_type
BED_NAMES = ", ".join(n for n, _ in BED_TYPES)


def _slot_int(ps, key, slot):
    """The integer value of a per-filament setting for 1-based slot, or None when missing or not a number. A list
    of one value (or a scalar) holds for every slot, as the scripts write a scalar for a list setting."""
    v = ps.get(key)
    if isinstance(v, list):
        v = v[0] if len(v) == 1 else (v[slot - 1] if 0 < slot <= len(v) else None)
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    n = to_int(v, None)
    if n is None and len(str(v).strip()) <= MAX_INT_CHARS:
        try:
            f = float(str(v).strip())
            n = int(f) if math.isfinite(f) and f == int(f) else None
        except (TypeError, ValueError, OverflowError):
            n = None
    return n


def bed_check(ps, slots, presets=None):
    """The build plate (curr_bed_type) and the bed temperatures it gives each filament slot in slots.
    Returns (info, errors, warnings): info = {"curr_bed_type", "plate", "key", "temps": [{"slot", "preset",
    "first_layer", "other_layers"}]}; errors are lines for a plate the filament cannot use (temperature 0, which
    the Bambu Studio CLI refuses to slice), a temperature missing for the chosen plate, or a plate name these checks do
    not know; warnings are lines for a curr_bed_type that is not set. Strings from the file are sanitized."""
    presets = presets or []
    raw = ps.get("curr_bed_type")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    given = None if raw is None or str(raw).strip() == "" else str(raw).strip()
    errors, warnings = [], []
    info = {"curr_bed_type": None if given is None else safe_text(given, 40), "plate": None, "key": None,
            "temps": []}
    if given is None:
        warnings.append("curr_bed_type (the build plate) is not set: the Bambu Studio CLI then uses %s, so the "
                        "bed temperature may not be the one for the plate on the printer; set curr_bed_type to "
                        "that plate (%s)" % (CLI_DEFAULT_BED, BED_NAMES))
        plate = CLI_DEFAULT_BED
    else:
        plate = given
    key = dict(BED_TYPES).get(plate)
    if key is None:
        errors.append("curr_bed_type %r is not a plate these checks know (%s): Bambu Studio may use another bed "
                      "temperature than you expect; set it to the plate on the printer"
                      % (safe_text(given, 40), BED_NAMES))
        return info, errors, warnings
    info["plate"], info["key"] = plate, key
    why = "" if given is not None else " (curr_bed_type is not set, so the CLI uses %s)" % CLI_DEFAULT_BED
    for s in slots:
        first, other = _slot_int(ps, key + "_initial_layer", s), _slot_int(ps, key, s)
        name = safe_text(presets[s - 1], 60) if 0 < s <= len(presets) and presets[s - 1] is not None else "?"
        info["temps"].append({"slot": s, "preset": name, "first_layer": first, "other_layers": other})
        zero = [k for k, v in ((key + "_initial_layer", first), (key, other)) if v == 0]
        missing = [k for k, v in ((key + "_initial_layer", first), (key, other)) if v is None]
        if zero:
            errors.append("filament %d (%s): %s %s 0 on %s%s - this filament is not made for that plate and the "
                          "Bambu Studio CLI refuses to slice it; use the plate the filament supports, or set "
                          "curr_bed_type to the plate really on the printer"
                          % (s, name, " and ".join(zero), "are" if len(zero) > 1 else "is", plate, why))
        elif missing and given is not None:
            errors.append("filament %d (%s): no %s in the project settings, so the bed temperature on %s is not "
                          "known" % (s, name, " or ".join(missing), plate))
    return info, errors, warnings


def layer_height_key(v):
    """A layer height as a comparable label: '0.20', '0.2' and 0.2 are all '0.2'."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(f) or math.isinf(f):
        return str(v)
    return "%g" % round(f, 4)


def layer_height_check(objs, ps, plate_of):
    """Problems where a prime tower would print objects of different layer heights together.
    Returns a list of (plate, message). Bambu Studio refuses to slice that ("different layer heights")."""
    problems = []
    if str(ps.get("enable_prime_tower", "0")) not in ("1", "true"):
        return problems
    by_plate = {}
    for o in objs:
        by_plate.setdefault(plate_of.get(o["id"], 1), []).append(o)
    for pl, os_ in sorted(by_plate.items()):
        fil = set()
        for o in os_:
            fil |= object_filaments(o)
        if len(fil) < 2:
            continue
        lh = {}
        for o in os_:
            lh.setdefault(layer_height_key(effective(o, ps, "layer_height")), []).append(safe_text(o["name"], 60))
        if len(lh) > 1:
            problems.append((pl, "prime tower on with %d filaments but layer heights differ: %s" % (
                len(fil), "; ".join("%s mm: %s" % (safe_text(k, 20), ", ".join(v)) for k, v in sorted(lh.items())))))
    return problems


def known_keys(sp=None, ptype=None):
    """Setting keys we know Bambu Studio uses: the bundled list plus every key in the install's presets.
    Never extend this from a project file: a downloaded project is not a source of truth."""
    ks = set()
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "known_setting_keys.txt")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            ks |= {l.strip() for l in f if l.strip() and not l.startswith("#")}
    if sp is not None:
        for d in sp.by_name.values():
            if ptype is None or d["_type"] == ptype:
                ks |= {k for k in d if not k.startswith("_")}
    return {k for k in ks if SETTING_KEY_RE.match(k)}


def gcode_text(v):
    """G-code setting value as one comparable string (lists joined, line ends normalised, trimmed)."""
    return _gcode_norm(v)


def _gcode_norm(v):
    if isinstance(v, list):
        v = "\n".join(str(x) for x in v)
    return str(v or "").replace("\r\n", "\n").strip()


def custom_gcode(ps, sp):
    """Compare a project's non-empty *_gcode settings with the system presets it names.
    Returns (differs, unchecked): labels of keys whose G-code differs from the install's preset, and of keys
    that could not be compared (no install, or the project names a preset the install does not have)."""
    keys = sorted(k for k in ps if k.endswith("_gcode") and _gcode_norm(ps[k]))
    if not keys:
        return [], []
    if sp is None:
        return [], keys
    printer = ps.get("printer_settings_id")
    printer = printer[0] if isinstance(printer, list) and printer else printer
    machine = sp.resolve(printer) if isinstance(printer, str) else None
    fids = ps.get("filament_settings_id") or []
    fids = [fids] if isinstance(fids, str) else fids
    differs, unchecked = [], []
    for k in keys:
        v = ps[k]
        if k.startswith("filament_"):
            for i, val in enumerate(v if isinstance(v, list) else [v]):
                if not _gcode_norm(val):
                    continue
                fp = sp.resolve(fids[i]) if i < len(fids) and isinstance(fids[i], str) else None
                label = "%s (filament %d)" % (k, i + 1)
                if fp is None:
                    unchecked.append(label)
                    continue
                ref = fp.get(k)
                ref = ref[0] if isinstance(ref, list) and ref else ref
                if _gcode_norm(val) != _gcode_norm(ref):
                    differs.append(label)
        elif machine is None:
            unchecked.append(k)
        elif _gcode_norm(v) != _gcode_norm(machine.get(k)):
            differs.append(k)
    return differs, unchecked


# ---------------------------------------------------------------- 3MF geometry (bounding boxes)

IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
ITEM_ATTRS = ("objectid", "transform", "printable")  # all a build item keeps (D9)



def tf(s):
    """A 3MF transform attribute (12 numbers) as a list; identity when absent."""
    if not s:
        return list(IDENTITY)
    try:
        v = [float(x) for x in s.split()]
    except ValueError:
        raise GeometryError("a transform is not 12 numbers")
    if len(v) != 12:
        raise GeometryError("a transform has %d numbers, not 12" % len(v))
    return v


def ap(m, v):
    x, y, z = v
    return (m[0] * x + m[3] * y + m[6] * z + m[9], m[1] * x + m[4] * y + m[7] * z + m[10],
            m[2] * x + m[5] * y + m[8] * z + m[11])


def mul(a, b):  # apply b, then a (3MF row-vector convention)
    A = [[a[0], a[1], a[2], 0], [a[3], a[4], a[5], 0], [a[6], a[7], a[8], 0], [a[9], a[10], a[11], 1]]
    B = [[b[0], b[1], b[2], 0], [b[3], b[4], b[5], 0], [b[6], b[7], b[8], 0], [b[9], b[10], b[11], 1]]
    C = [[sum(B[i][k] * A[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
    return [C[0][0], C[0][1], C[0][2], C[1][0], C[1][1], C[1][2], C[2][0], C[2][1], C[2][2], C[3][0], C[3][1], C[3][2]]


class Geometry:
    """Bounding boxes of 3MF objects, following components (objects made of other objects) with limits:
    no cycles, at most MAX_COMPONENT_DEPTH levels, MAX_COMPONENT_VISITS uses and MAX_VERTICES vertices in all.
    Each .model file is read in one streaming expat pass (no regex over the file); reading stops with a
    GeometryError as soon as the files hold more than MAX_VERTICES vertices or one is not well-formed XML.
    The archive budget (D9; a fresh Budget when none is given) caps the objects, components and build items
    stored across all files.
    items: {"3d/3dmodel.model": [{objectid, transform, printable} of each <item> in its <build>]}: only the main
    model's build items are stored, at most MAX_BUILD_ITEMS, each with those three attributes only.
    triangles=True also keeps each mesh's triangles (vertex indices, checked against that mesh's vertices) and
    each object's name attribute, for placed_meshes(); at most max_triangles (never more than MAX_TRIANGLES) are
    stored, and placed_meshes() places at most max_triangles in all (on top of the cycle, depth, visit and
    vertex limits)."""

    def __init__(self, models=None, triangles=False, max_triangles=None, budget=None):
        self.meshes = {}   # (file, object id) -> array of x, y, z
        self.comps = {}    # (file, object id) -> [(object id, file or None, transform or None)]
        self.items = {}
        self.stored = 0
        self.visits = 0
        self.vertices = 0
        self.collect = triangles
        self.budget = Budget() if budget is None else budget
        self.max_triangles = MAX_TRIANGLES if max_triangles is None else min(max_triangles, MAX_TRIANGLES)
        self.tris = {}     # triangles mode: (file, object id) -> array of v1, v2, v3
        self.names = {}    # triangles mode: (file, object id) -> the object's name attribute
        self.stored_tris = 0
        self.placed_tris = 0
        for fn, data in (models or {}).items():
            self.add(fn, data)

    def add(self, fn, data):
        fn = norm_name(fn)
        objs, build = [], [0]
        items = self.items.setdefault(fn, []) if fn == norm_name(MAIN_MODEL) else None
        budget = self.budget
        charge = budget.charge
        # B3: every element is counted, and every element but <vertex> and <triangle> again, against what is left
        # of the archive's caps; kept here and charged once at the end, so the hot path is two additions
        seen = [0, 0]
        left = (budget.cap("model_elements") - budget.used["model_elements"],
                budget.cap("model_other") - budget.used["model_other"])

        def keep(*vals):
            """Charge the characters of kept attribute values (one more for each value; their number is capped
            by the object, component and build-item caps)."""
            charge("model_chars", sum(len(v) + 1 for v in vals if v is not None), error=GeometryError)

        def start(tag, a, i):
            seen[0] += 1
            if tag != "vertex" and tag != "triangle":
                seen[1] += 1
                if seen[1] > left[1]:
                    charge("model_other", seen[1], error=GeometryError)
            if seen[0] > left[0]:
                charge("model_elements", seen[0], error=GeometryError)
            if tag == "object":
                charge("model_objects", error=GeometryError)
                objs.append((fn, a.get("id", "")))
                keep(objs[-1][1])
                if self.collect:
                    if objs[-1] in self.names:
                        raise GeometryError("two objects have id %s" % safe_text(objs[-1][1], 20))
                    self.names[objs[-1]] = a.get("name")
                    keep(a.get("name"))
            elif not objs:
                if tag == "build":
                    build[0] += 1
                elif tag == "item" and build[0] and items is not None:
                    charge("build_items", error=GeometryError)
                    items.append(dict((k, a[k]) for k in ITEM_ATTRS if k in a))
                    keep(*items[-1].values())
            elif tag == "vertex":
                try:
                    xyz = (float(a["x"]), float(a["y"]), float(a["z"]))
                except (KeyError, ValueError):
                    raise GeometryError("object %s has a vertex without numeric x, y, z" % safe_text(objs[-1][1], 20))
                self.stored += 1
                if self.stored > MAX_VERTICES:
                    raise GeometryError("the project has more than %d vertices" % MAX_VERTICES)
                self.meshes.setdefault(objs[-1], array("d")).extend(xyz)
            elif tag == "triangle" and self.collect:
                self.add_triangle(objs[-1], a)
            elif tag == "mesh":
                self.meshes.setdefault(objs[-1], array("d"))
            elif tag == "component":
                charge("components", error=GeometryError)
                pth = next((v for k, v in a.items() if k == "path" or k.endswith(":path")), None)
                self.comps.setdefault(objs[-1], []).append((a.get("objectid", ""), pth, a.get("transform")))
                keep(*self.comps[objs[-1]][-1])

        def end(tag, i):
            if tag == "object" and objs:
                objs.pop()
            elif tag == "build" and not objs:
                build[0] -= 1

        try:
            walk_xml(data, safe_text(fn, 80), start, end)
        except BadEntry as e:
            raise GeometryError(str(e))
        charge("model_elements", seen[0], error=GeometryError)
        charge("model_other", seen[1], error=GeometryError)

    def add_triangle(self, key, a):
        try:
            v1, v2, v3 = a["v1"], a["v2"], a["v3"]
            if len(v1) > MAX_INT_CHARS or len(v2) > MAX_INT_CHARS or len(v3) > MAX_INT_CHARS:
                raise ValueError(v1)
            t = (int(v1), int(v2), int(v3))
        except (KeyError, ValueError):
            raise GeometryError("object %s has a triangle without integer v1, v2, v3" % safe_text(key[1], 20))
        if min(t) < 0 or max(t) >= len(self.meshes.get(key, ())) // 3:
            raise GeometryError("object %s has a triangle that points past its vertices" % safe_text(key[1], 20))
        self.stored_tris += 1
        if self.stored_tris > self.max_triangles:
            raise GeometryError("the project has more than %d triangles" % self.max_triangles)
        self.tris.setdefault(key, array("I")).extend(t)

    def placed_meshes(self, fn, oid, M):
        """Triangles mode: [(file, object id, M, vertices, triangles)] for every mesh in object oid of file fn and
        its components, M being where that mesh is placed (12 numbers, as tf()). The same limits as bbox(), plus
        at most max_triangles placed in all (counted across calls, like the vertices)."""
        if not self.collect:
            raise BambuError("placed_meshes needs Geometry(triangles=True)")
        out = []
        self._walk_meshes(norm_name(fn), oid, M, out, ())
        return out

    def _walk_meshes(self, fn, oid, M, out, stack):
        key = (fn, oid)
        if key in stack:
            raise GeometryError("its components refer back to themselves (object %s)" % safe_text(oid, 20))
        if len(stack) > MAX_COMPONENT_DEPTH:
            raise GeometryError("its components are nested more than %d levels deep" % MAX_COMPONENT_DEPTH)
        self.visits += 1
        if self.visits > MAX_COMPONENT_VISITS:
            raise GeometryError("the project has more than %d component uses" % MAX_COMPONENT_VISITS)
        vs, ts = self.meshes.get(key), self.tris.get(key)
        if vs and ts:
            self.vertices += len(vs) // 3
            if self.vertices > MAX_VERTICES:
                raise GeometryError("the project has more than %d vertices to place" % MAX_VERTICES)
            self.placed_tris += len(ts) // 3
            if self.placed_tris > self.max_triangles:
                raise GeometryError("the project has more than %d triangles to place" % self.max_triangles)
            out.append((fn, oid, M, vs, ts))
        stack = stack + (key,)
        for cid, pth, ct in self.comps.get(key, []):
            if not ID_RE.match(cid):
                continue
            cfn = norm_name(pth.lstrip("/")) if pth else fn
            self._walk_meshes(cfn, cid, mul(M, tf(ct)), out, stack)

    def bbox(self, fn, oid, M):
        """[xmin, xmax, ymin, ymax, zmin, zmax] in world space, or None when the object has no vertices."""
        box = [math.inf, -math.inf, math.inf, -math.inf, math.inf, -math.inf]
        self._walk(norm_name(fn), oid, M, box, ())
        return None if box[0] == math.inf else box

    def _walk(self, fn, oid, M, box, stack):
        key = (fn, oid)
        if key in stack:
            raise GeometryError("its components refer back to themselves (object %s)" % safe_text(oid, 20))
        if len(stack) > MAX_COMPONENT_DEPTH:
            raise GeometryError("its components are nested more than %d levels deep" % MAX_COMPONENT_DEPTH)
        self.visits += 1
        if self.visits > MAX_COMPONENT_VISITS:
            raise GeometryError("the project has more than %d component uses" % MAX_COMPONENT_VISITS)
        vs = self.meshes.get(key)
        if vs:
            self.vertices += len(vs) // 3
            if self.vertices > MAX_VERTICES:
                raise GeometryError("the project has more than %d vertices to place" % MAX_VERTICES)
            for i in range(0, len(vs), 3):
                x, y, z = ap(M, (vs[i], vs[i + 1], vs[i + 2]))
                box[0], box[1] = min(box[0], x), max(box[1], x)
                box[2], box[3] = min(box[2], y), max(box[3], y)
                box[4], box[5] = min(box[4], z), max(box[5], z)
        stack = stack + (key,)
        for cid, pth, ct in self.comps.get(key, []):
            if not ID_RE.match(cid):
                continue
            cfn = norm_name(pth.lstrip("/")) if pth else fn
            self._walk(cfn, cid, mul(M, tf(ct)), box, stack)
