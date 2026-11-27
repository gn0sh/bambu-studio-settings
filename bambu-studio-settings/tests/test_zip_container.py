"""ADR 0006 D8 regression tests: the zip container layer. The scripts must see exactly the entries Bambu Studio's
miniz sees, or refuse the file (problem family untrusted-3mf/post-process-passthrough, finding 1 at 8ac5249).

The archives are built byte by byte (bss_fixtures.write_raw_zip_bytes), so each test controls the layout."""
import json
import os
import struct
import unittest
import zipfile
import zlib
from unittest import mock

import bss_fixtures as fx
import bambu_common as bc
from test_untrusted_3mf import EVIL, ONE_OBJECT, PS, Untrusted3mfBase

LAYOUT = "the zip structure is not one Bambu Studio writes"
SKIFF = "/mnt/project-files/maker/skiff/skiff_assembled.3mf"  # our own lib3mf-written model


def entries(ps=None, **kw):
    return [fx.zip_entry(n, d, **kw) for n, d, _ in fx.project_entries(ps=ps, objects=ONE_OBJECT)]


def miniz_read(path, name):
    """The bytes of one entry, found the way miniz finds them: the end record's directory offset as written,
    `total` records from there (no CPython offset shift)."""
    with open(path, "rb") as f:
        raw = f.read()
    total, cd_size, cd_offset = struct.unpack("<HII", raw[-12:-2])
    pos = cd_offset
    for _ in range(total):
        method, csize, nlen, xlen, clen, offset = (struct.unpack("<10xH8xI4xHHH8xI", raw[pos:pos + 46]))
        n = raw[pos + 46:pos + 46 + nlen]
        pos += 46 + nlen + xlen + clen
        if n == name.encode():
            lnlen, lxlen = struct.unpack("<HH", raw[offset + 26:offset + 30])
            data = raw[offset + 30 + lnlen + lxlen:offset + 30 + lnlen + lxlen + csize]
            return zlib.decompress(data, -15) if method == 8 else data
    return None


class ZipContainerTest(Untrusted3mfBase):
    def raw_bytes(self, name, ents, **kw):
        return fx.write_raw_zip_bytes(self.path(name), ents, **kw)

    def assertRefusedFast(self, src, why):
        """open_project refuses (no subprocess; assertRefused checks verify, tune and slice_check)."""
        with self.assertRaises(bc.RefusedArchive) as cm:
            bc.open_project(src)
        self.assertIn(LAYOUT, str(cm.exception))
        self.assertIn(why, str(cm.exception))

    # ------------------------------------------------------------ finding 1: two central directories (Z4)

    def two_directories(self):
        """Finding 1's layout: the directory miniz reads (at the end record's offset) lists a project settings
        entry with post_process; the one CPython reads (just before the end record, offsets shifted by the gap)
        lists a clean one."""
        evil, clean = entries(fx.project_settings(post_process=[EVIL])), entries()
        loc_a, rec_a = fx.zip_locals(evil)
        cd_a = b"".join(rec_a)
        loc_b, rec_b = fx.zip_locals(clean, start=len(loc_a), shift=-len(cd_a))
        cd_b = b"".join(rec_b)
        self.assertEqual(len(cd_a), len(cd_b))
        x = len(loc_a) + len(loc_b)
        path = self.path("two_cd.3mf")
        with open(path, "wb") as f:
            f.write(loc_a + loc_b + cd_a + cd_b + fx.zip_eocd(len(evil), len(cd_b), x))
        return path

    def test_two_central_directories_refused(self):
        src = self.two_directories()
        with zipfile.ZipFile(src) as z:  # the layout fools CPython: it sees only the clean copy
            self.assertEqual(json.loads(z.read(PS))["post_process"], [])
        self.assertIn(EVIL, json.loads(miniz_read(src, PS))["post_process"])  # what Bambu Studio would load
        self.assertRefused(src, LAYOUT)
        self.assertRefusedFast(src, "not where the end record says")

    def test_prepended_bytes_refused(self):
        src = self.raw_bytes("plain.3mf", entries())
        with open(src, "rb") as f:
            data = f.read()
        pre = self.path("pre.3mf")
        with open(pre, "wb") as f:
            f.write(b"MZ stub " * 8 + data)
        with zipfile.ZipFile(pre) as z:
            self.assertEqual(len(z.namelist()), 4)  # CPython reads it (offset shift)
        self.assertRefusedFast(pre, "not where the end record says")

    # ------------------------------------------------------------ Z1: the end record is the last 22 bytes

    def test_comment_and_trailing_bytes_refused(self):
        self.assertRefused(self.raw_bytes("comment.3mf", entries(), comment=b"hi"), LAYOUT)
        self.assertRefusedFast(self.raw_bytes("comment2.3mf", entries(), comment=b"hi"), "zip comment")
        self.assertRefusedFast(self.raw_bytes("tail.3mf", entries(), suffix=b"x"), "last 22 bytes")

    # ------------------------------------------------------------ Z5/Z6: records and counts

    def test_directory_bytes_after_records_refused(self):
        self.assertRefusedFast(self.raw_bytes("cdtail.3mf", entries(), cd_tail=b"\0" * 46), "bytes after its")

    def test_end_record_counts_one_less_refused(self):
        src = self.raw_bytes("count.3mf", entries(), eocd={"total": 3, "here": 3})
        self.assertRefusedFast(src, "bytes after its 3 counted records")

    # ------------------------------------------------------------ Z3: zip64 only where every reader agrees

    def test_zip64_refused(self):
        locator = b"PK\x06\x07" + b"\0" * 16
        self.assertRefusedFast(self.raw_bytes("loc.3mf", entries(), before_eocd=locator), "zip64 locator")
        ents = entries()
        ents[2]["cd_extra"] = struct.pack("<HH", 0x0001, 8) + struct.pack("<Q", 100)  # no field is 0xFFFFFFFF
        self.assertRefusedFast(self.raw_bytes("x1.3mf", ents), "zip64 extra field that does not match")
        ents = entries()
        ents[2]["cd"]["usize"] = 0xFFFFFFFF  # a placeholder with no zip64 extra field
        self.assertRefusedFast(self.raw_bytes("ff.3mf", ents), "zip64 extra field that does not match")
        src = self.raw_bytes("ffeocd.3mf", entries(), eocd={"cd_offset": 0xFFFFFFFF})  # no zip64 end record
        self.assertRefusedFast(src, "zip64 fields without a zip64 end record")

    # Deviation 53: what lib3mf writes (build123d's Mesher, our skiff model). Every directory record has
    # 0xFFFFFFFF sizes and offset and a 24-byte zip64 extra field, every local header a 16-byte one, and a zip64
    # end record and locator sit before an end record whose directory offset is 0xFFFFFFFF.

    def lib3mf_zip(self, name, ents=None, rec=None, loc=None, eocd=None, cd_extra=None,
                   placeholders=("usize", "csize", "offset")):
        """A project written lib3mf's way. rec, loc, eocd: field overrides of the zip64 end record (here, total,
        cd_size, cd_offset, size, disk), its locator (disk, offset, disks) and the classic end record (its
        cd_offset is the placeholder unless set, None for the real one); cd_extra: {entry index: bytes}
        replacing a zip64 extra field; placeholders: which of the record's three fields are 0xFFFFFFFF with their
        real value in the extra field (deviation 57)."""
        ents = entries() if ents is None else ents
        for e in ents:
            e["local_extra"] = struct.pack("<HHQQ", 1, 16, 0, 0)
        body, recs = fx.zip_locals(ents)
        for n, (e, r) in enumerate(zip(ents, recs)):
            csize, usize = struct.unpack("<II", r[20:28])
            offset, = struct.unpack("<I", r[42:46])
            e["cd"].update((k, bc.U32) for k in placeholders)
            wide = [v for k, v in (("usize", usize), ("csize", csize), ("offset", offset)) if k in placeholders]
            e["cd_extra"] = (cd_extra or {}).get(n, struct.pack("<HH%dQ" % len(wide), 1, 8 * len(wide), *wide))
        body, recs = fx.zip_locals(ents)
        cd_size, cd_offset = sum(map(len, recs)), len(body)
        f = {"here": len(ents), "total": len(ents), "cd_size": cd_size, "cd_offset": cd_offset, "size": 44,
             "disk": 0}
        f.update(rec or {})
        z64 = struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", f["size"], 45, 45, f["disk"], 0, f["here"], f["total"],
                          f["cd_size"], f["cd_offset"])
        g = {"disk": 0, "offset": cd_offset + cd_size, "disks": 1}
        g.update(loc or {})
        locator = struct.pack("<4sIQI", b"PK\x06\x07", g["disk"], g["offset"], g["disks"])
        h = {"cd_offset": bc.U32}
        h.update(eocd or {})
        if h["cd_offset"] is None:
            h["cd_offset"] = cd_offset
        return self.raw_bytes(name, ents, before_eocd=z64 + locator, eocd=h)

    def test_lib3mf_zip64_accepted(self):
        src = self.lib3mf_zip("lib3mf.3mf")
        plain = self.raw("plain.3mf", fx.project_entries(objects=ONE_OBJECT))
        with zipfile.ZipFile(src) as z, zipfile.ZipFile(plain) as p:
            self.assertEqual(z.read(PS), p.read(PS))
        with bc.open_project(src) as idx:
            self.assertEqual((idx.findings, idx.unsafe), ([], []))
        a, b = self.verify(src, "--json"), self.verify(plain, "--json")
        self.assertEqual(a.returncode, 0, a.stdout + a.stderr)
        ja, jb = json.loads(a.stdout), json.loads(b.stdout)
        ja.pop("project"), jb.pop("project")
        self.assertEqual(ja, jb)
        # each classic field may be its placeholder or the real value
        for eocd in ({"here": 0xFFFF, "total": 0xFFFF, "cd_size": bc.U32}, {"cd_offset": None}):
            with self.subTest(eocd), bc.open_project(self.lib3mf_zip("ph.3mf", eocd=eocd)) as idx:
                self.assertEqual(idx.findings, [])

    def test_lib3mf_post_process_still_found(self):
        ents = entries(fx.project_settings(post_process=[EVIL]))
        with bc.open_project(self.lib3mf_zip("evil64.3mf", ents)) as idx:
            self.assertEqual([f.value for f in idx.findings], [EVIL])

    def test_cpython_force_zip64_accepted(self):
        """CPython's zipfile with force_zip64 puts zip64 extras in the local headers only: read as before."""
        src = self.path("forced.3mf")
        with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
            for n, d, _ in fx.project_entries(objects=ONE_OBJECT):
                with z.open(n, "w", force_zip64=True) as w:
                    w.write(d.encode("utf-8") if isinstance(d, str) else d)
        with bc.open_project(src) as idx:
            self.assertEqual(idx.findings, [])

    def test_inconsistent_zip64_refused(self):
        n = len(entries())
        one = struct.pack("<HHQQQ", 1, 24, 1, 1, 1)
        cases = {
            "rec_here": ({"rec": {"here": n - 1}}, "inconsistent entry counts in the zip64 end record"),
            "rec_total": ({"rec": {"here": n + 1, "total": n + 1}}, "does not agree with the end record"),
            "rec_offset": ({"rec": {"cd_offset": 0}, "eocd": {"cd_offset": 0}}, "not where the end record says"),
            "rec_vs_classic": ({"eocd": {"cd_size": 46 * n}}, "does not agree with the end record"),
            "rec_ext_data": ({"rec": {"size": 52}}, "extensible data"),
            "rec_disk": ({"rec": {"disk": 1}}, "multi-disk"),
            "loc_zero": ({"loc": {"offset": 0}}, "locator does not point right before itself"),
            "loc_past_end": ({"loc": {"offset": 2 ** 63}}, "locator does not point right before itself"),
            "loc_disks": ({"loc": {"disks": 2}}, "multi-disk zip64 locator"),
            "extra_short": ({"cd_extra": {2: struct.pack("<HHQQ", 1, 16, 1, 1)}}, "does not match directory record 3"),
            "extra_twice": ({"cd_extra": {2: one + one}}, "does not match directory record 3"),
            "csize_past_end": ({"cd_extra": {2: struct.pack("<HHQQQ", 1, 24, 10, 2 ** 40, 0)}}, "runs past the end"),
        }
        for label, (kw, why) in cases.items():
            with self.subTest(label):
                self.assertRefusedFast(self.lib3mf_zip("%s.3mf" % label, **kw), why)

    def test_zip64_offset_without_compressed_size_refused(self):
        """Deviation 57: miniz adds the raw 0xFFFFFFFF offset to the compressed size and cannot open the file,
        while CPython reads it; refused. The other single and double placeholders are read by all three."""
        for ph in (("offset",), ("usize", "offset")):
            with self.subTest(ph):
                src = self.lib3mf_zip("offset_only.3mf", placeholders=ph)
                self.assertRefusedFast(src, "a zip64 offset without a zip64 compressed size in directory record 1")
        for ph in (("usize",), ("csize",), ("usize", "csize"), ("csize", "offset"), ("usize", "csize", "offset")):
            with self.subTest(ph), bc.open_project(self.lib3mf_zip("ok.3mf", placeholders=ph)) as idx:
                self.assertEqual(idx.findings, [])

    def test_zip64_values_over_the_caps_refused_before_zipfile(self):
        over = struct.pack("<HHQQQ", 1, 24, bc.MAX_ENTRY_BYTES + 1, 10, 0)
        shapes = (({"cd_extra": {2: over}}, "entry 3 is 512 MB uncompressed; the limit is 512 MB"),
                  ({"rec": {"here": 2 ** 40, "total": 2 ** 40}, "eocd": {"here": 0xFFFF, "total": 0xFFFF}},
                   "the central directory is too small"),
                  ({"rec": {"cd_size": 2 ** 40}, "eocd": {"cd_size": bc.U32}}, "larger than 16 MB"))
        for kw, why in shapes:
            src = self.lib3mf_zip("caps.3mf", **kw)
            with self.subTest(why), self.assertRaises(bc.RefusedArchive) as cm, \
                    mock.patch.object(bc.zipfile, "ZipFile", side_effect=AssertionError("ZipFile was built")):
                bc.open_project(src)
            self.assertIn(why, str(cm.exception))

    @unittest.skipUnless(os.path.isfile(SKIFF), "the skiff model is not on this machine")
    def test_real_lib3mf_file_read(self):
        """Our own build123d model (lib3mf writer), which D8 refused before deviation 53."""
        with bc.open_project(SKIFF) as idx:
            self.assertEqual(len(idx.zip.infolist()), 3)
            self.assertGreater(idx.geometry().stored, 0)

    # ------------------------------------------------------------ Z2: plain single-disk fields

    def test_disk_numbers_refused(self):
        src = self.raw_bytes("disk.3mf", entries(), eocd={"disk": 1, "cd_disk": 1})
        self.assertRefusedFast(src, "multi-disk")

    def test_large_directory_refused_before_zipfile_reads_it(self):
        src = self.raw_bytes("big_cd.3mf", entries())
        with mock.patch.object(bc, "MAX_CD_BYTES", 100), \
                mock.patch.object(bc.zipfile, "ZipFile", side_effect=AssertionError("ZipFile was built")):
            self.assertRefusedFast(src, "central directory is larger than")

    # ------------------------------------------------------------ Z7: methods and flags

    def test_method_and_encryption_refused(self):
        ents = entries()
        ents[2]["method"] = 12  # bzip2: miniz extracts nothing but stored and deflate
        self.assertRefused(self.raw_bytes("bz.3mf", ents), "compression method 12")
        ents = entries()
        ents[2]["flags"] = 0x1
        self.assertRefusedFast(self.raw_bytes("enc.3mf", ents), "encrypted")

    def test_other_record_checks(self):
        cases = {
            "stored_sizes": ({"method": 0}, {"usize": 5}, "sizes that do not fit"),
            "zero_csize": ({}, {"csize": 0}, "sizes that do not fit"),
            "past_end": ({}, {"offset": 10 ** 6}, "runs past the end"),
            "masked": ({"flags": 0x2000}, {}, "masked"),
            "disk_start": ({}, {"disk": 1}, "another disk"),
            "no_local": ({}, {"offset": 1}, "no local header"),
        }
        for label, (kw, cd, why) in cases.items():
            ents = entries()
            ents[2].update(kw)
            ents[2]["cd"].update(cd)
            self.assertRefusedFast(self.raw_bytes("%s.3mf" % label, ents), why)

    def test_not_a_zip_is_a_refusal_without_resave_advice(self):
        # L3 (supersedes deviation 10): a refusal, so verify reports it as a PROBLEM and in --json; but not
        # "re-save it in Bambu Studio", which would be wrong advice for a file that is not a zip at all
        p = self.path("text.3mf")
        with open(p, "w") as f:
            f.write("hello, this is not a zip at all" * 3)
        with self.assertRaises(bc.RefusedArchive) as cm:
            bc.open_project(p)
        self.assertIn("not a zip", str(cm.exception))
        self.assertNotIn("save it as a new project", str(cm.exception))

    # ------------------------------------------------------------ names from the directory bytes (Z6 + D1)

    def test_nul_in_directory_name_collides(self):
        ents = entries() + [fx.zip_entry(b"Metadata/project_settings.config\0x",
                                         json.dumps(fx.project_settings(post_process=[EVIL])))]
        self.assertRefused(self.raw_bytes("nul.3mf", ents), "same name when case and slashes are ignored")

    # ------------------------------------------------------------ raw names in messages (deviation 57)

    @staticmethod
    def unicode_path_extra(raw, shown):
        """A 0x7075 Unicode-path extra field naming `shown` for the entry named `raw`: CPython 3.12+ puts it in
        ZipInfo.filename, CPython 3.8 and miniz ignore it."""
        body = struct.pack("<BI", 1, zlib.crc32(raw) & 0xFFFFFFFF) + shown
        return struct.pack("<HH", 0x7075, len(body)) + body

    def test_messages_show_the_raw_directory_name(self):
        real, fake = b"Metadata/process_settings_1.config", b"Metadata/harmless_thumbnail.png"
        extra = self.unicode_path_extra(real, fake)
        preset = json.dumps({"name": "p", "machine_start_gcode": "M104 S250", "wall_loops": "2"})
        ents = entries() + [fx.zip_entry(real, preset, cd_extra=extra)]
        src = self.raw_bytes("upath.3mf", ents)
        with zipfile.ZipFile(src) as z:  # 3.12+: the attacker's name; 3.8: the real one (the test holds on both)
            self.assertIn(z.infolist()[-1].filename.encode(), (real, fake))
        with bc.open_project(src) as idx:
            self.assertEqual([raw for _, _, _, raw in idx.presets], [real.decode()])
        r = self.verify(src)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("process_settings_1.config", r.stdout)
        self.assertNotIn("harmless_thumbnail", r.stdout)
        # check_zip and read_entry messages: a zip bomb entry named by the same trick
        bomb = fx.zip_entry(real, b"\0" * (4 * 1024 * 1024), cd_extra=extra)
        bad = self.raw_bytes("upath_bomb.3mf", entries() + [bomb])
        with self.assertRaises(bc.RefusedArchive) as cm:
            bc.open_project(bad)
        self.assertIn(real.decode(), str(cm.exception))
        self.assertNotIn("harmless_thumbnail", str(cm.exception))
        with zipfile.ZipFile(src) as z:
            info = z.infolist()[-1]
            with self.assertRaises(bc.BambuError) as cm:
                bc.read_entry(z, info, cap=1)
            self.assertIn(real.decode(), str(cm.exception))
            self.assertNotIn("harmless_thumbnail", str(cm.exception))

    # ------------------------------------------------------------ guards against over-refusal

    def test_bambu_shaped_project_accepted(self):
        """What Bambu Studio writes: streamed entries (data descriptor), a 0x9999 placeholder in the 3D model's
        local header, no zip64 in the directory. Same verify result as the zipfile-written fixture."""
        placeholder = struct.pack("<HH", 0x9999, 16) + b"\0" * 16
        ents = entries(flags=0x8)
        ents[1]["local_extra"] = placeholder
        bambu = self.raw_bytes("bambu.3mf", ents)
        plain = self.raw("plain.3mf", fx.project_entries(objects=ONE_OBJECT))
        with bc.open_project(bambu) as idx:
            self.assertEqual((idx.findings, idx.unsafe), ([], []))
        a, b = self.verify(bambu, "--json"), self.verify(plain, "--json")
        self.assertEqual(a.returncode, 0, a.stdout + a.stderr)
        ja, jb = json.loads(a.stdout), json.loads(b.stdout)
        ja.pop("project"), jb.pop("project")
        self.assertEqual(ja, jb)
        out = self.tune_ok(bambu)
        self.assertEqual(self.findings(out), [])
