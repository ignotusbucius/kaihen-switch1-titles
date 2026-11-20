#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline tests for mkdonor.py. Run from tools/: python3 -m unittest -v test_mkdonor"""
import contextlib
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mkdonor  # noqa: E402


def write_exefs_package_reference(main, policy):
    """kaiHEN common/policy_package.cpp writeExeFsPackage, transcribed step by step (two files)."""
    header_size = 16 + 2 * 24 + len(b"main\0") + len(b"main.npdm\0")
    policy_offset = (len(main) + 0x1F) & ~0x1F
    header = bytearray(header_size)
    header[0:4] = b"PFS0"
    names_size = len(b"main\0") + len(b"main.npdm\0")
    struct.pack_into("<I", header, 4, 2)
    struct.pack_into("<I", header, 8, names_size)
    struct.pack_into("<Q", header, 16 + 8, len(main))
    struct.pack_into("<Q", header, 40, policy_offset)
    struct.pack_into("<Q", header, 48, len(policy))
    struct.pack_into("<I", header, 56, len(b"main\0"))
    header[64:64 + names_size] = b"main\0main.npdm\0"
    padding = b"\0" * (policy_offset - len(main))
    return bytes(header) + main + padding + policy


def nso(tag, size):
    """A fake module: the NSO0 magic and filler. No real code anywhere in these tests."""
    return b"NSO0" + bytes((tag + i) & 0xFF for i in range(max(size, 4) - 4))


def npdm(size):
    return b"META" + bytes(i & 0xFF for i in range(size - 4))


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


class Layout(unittest.TestCase):
    def test_two_files_match_writeexefspackage(self):
        for main_size in (4, 5, 31, 32, 33, 0x1000, 0x1001, 98761):
            main = nso(1, main_size)
            policy = npdm(0x724)
            ours = mkdonor.build_pfs0([("main", main), ("main.npdm", policy)])
            self.assertEqual(ours, write_exefs_package_reference(main, policy), main_size)

    def test_round_trip_many_files(self):
        files = [("rtld", nso(1, 10477)), ("main", nso(2, 98761)), ("subsdk0", nso(3, 33)),
                 ("subsdk1", nso(4, 64)), ("subsdk10", nso(5, 4)), ("sdk", nso(6, 7001)),
                 ("main.npdm", npdm(1828))]
        blob = mkdonor.build_pfs0(files)
        self.assertEqual(mkdonor.parse_pfs0(blob), files)
        count, table_size, reserved = struct.unpack_from("<III", blob, 4)
        self.assertEqual((count, reserved), (7, 0))
        data_start = 16 + 24 * count + table_size
        for i in range(count):
            offset, size, _, zero = struct.unpack_from("<QQII", blob, 16 + 24 * i)
            self.assertEqual(offset % 0x20, 0)
            self.assertEqual(zero, 0)
        self.assertEqual(len(blob), data_start + offset + size)  # the last file is unpadded

    def test_parse_refuses_truncation(self):
        blob = mkdonor.build_pfs0([("main", nso(1, 100)), ("main.npdm", npdm(300))])
        with self.assertRaises(mkdonor.DonorError):
            mkdonor.parse_pfs0(blob[:-1])
        with self.assertRaises(mkdonor.DonorError):
            mkdonor.parse_pfs0(b"PFS1" + blob[4:])

    def test_duplicate_names_refused(self):
        with self.assertRaises(mkdonor.DonorError):
            mkdonor.build_pfs0([("main", nso(1, 8)), ("main", nso(2, 8))])

    def test_order(self):
        names = ["main.npdm", "sdk", "subsdk10", "zzz", "subsdk2", "main", "rtld", "aaa"]
        self.assertEqual(sorted(names, key=mkdonor.order_key),
                         ["rtld", "main", "subsdk2", "subsdk10", "sdk", "aaa", "zzz", "main.npdm"])

    def test_fnv64(self):
        self.assertEqual(mkdonor.fnv64(b""), 0xCBF29CE484222325)
        self.assertEqual(mkdonor.fnv64(b"a"), 0xAF63DC4C8601EC8C)
        self.assertEqual(mkdonor.fnv64(b"foobar"), 0x85944171F73967E8)
        self.assertEqual(mkdonor.fnv64(b"bar", mkdonor.fnv64(b"foo")), mkdonor.fnv64(b"foobar"))

    def test_capture_names(self):
        for good in ("main", "main.npdm", "subsdk9", "..."):
            self.assertTrue(mkdonor.is_capture_name(good), good)
        for bad in ("", ".", "..", "a/b", "a\\b", "c:x", "sp ace", "x" * 65):
            self.assertFalse(mkdonor.is_capture_name(bad), bad)


class Tool(unittest.TestCase):
    TID = "0100123400abc000"  # a made-up Switch 1 application id

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cap = os.path.join(self.tmp.name, "s1", self.TID)
        os.makedirs(self.cap)
        self.title = {"main": nso(2, 5000), "rtld": nso(9, 777), "sdk": nso(3, 900),
                      "subsdk0": nso(4, 123), "subsdk1": nso(5, 45), "main.npdm": npdm(1124)}
        for n, b in self.title.items():
            with open(os.path.join(self.cap, n), "wb") as f:
                f.write(b)
        self.hbl = os.path.join(self.tmp.name, "hbloader")
        self.hbloader = nso(7, 4321)
        with open(self.hbl, "wb") as f:
            f.write(self.hbloader)
        self.out = os.path.join(self.tmp.name, "s1", self.TID + ".nsp")
        self.log = os.path.join(self.tmp.name, "hen.txt")
        program = "0x%x" % int(self.TID, 16)
        self.lines = ["capture open rc=0x0 program=%s storage=0x4 attr=0x0" % program,
                      "capture list rc=0x0 entries=0x6 read=0x6 limit=0x18"]
        for n, b in self.title.items():
            self.lines.append("capture file name=%s rc=0x0 type=0x1 size=0x%x written=0x%x fnv64=0x%x"
                              % (n, len(b), len(b), mkdonor.fnv64(b)))
        self.done = "capture done program=%s files=0x6 failed=0x0 bytes=0x1" % program
        self.write_log(self.lines + [self.done])

    def tearDown(self):
        self.tmp.cleanup()

    def write_log(self, lines):
        with open(self.log, "w") as f:
            f.write("\n".join(lines) + "\n")

    def run_tool(self, *extra, hbloader=None):
        args = [self.cap, "--hbloader", hbloader or self.hbl, *extra]
        if "--no-hen-log" not in extra and "--hen-log" not in extra:
            args += ["--hen-log", self.log]
        return quiet(mkdonor.main, args)

    def read_out(self):
        with open(self.out, "rb") as f:
            return mkdonor.parse_pfs0(f.read())

    def test_builds_title_shaped_donor(self):
        self.assertEqual(self.run_tool(), 0)
        files = self.read_out()
        self.assertEqual([n for n, _ in files], ["rtld", "main", "subsdk0", "subsdk1", "sdk", "main.npdm"])
        got = dict(files)
        self.assertEqual(got["rtld"], self.hbloader)  # the title's rtld is replaced
        for n in ("main", "subsdk0", "subsdk1", "sdk", "main.npdm"):
            self.assertEqual(got[n], self.title[n], n)
        self.assertEqual(self.run_tool(), 2)  # refuses to overwrite
        self.assertEqual(self.run_tool("--force"), 0)

    def test_hbloader_from_release_zip(self):
        zpath = os.path.join(self.tmp.name, "kaiHEN-test.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("game/main", self.hbloader)
            z.writestr("hbmenu.nro", b"not used")
        self.assertEqual(self.run_tool(hbloader=zpath), 0)
        self.assertEqual(dict(self.read_out())["rtld"], self.hbloader)
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("hbmenu.nro", b"no game/main")
        self.assertEqual(self.run_tool("--force", hbloader=zpath), 2)

    def test_expect_sha256(self):
        import hashlib
        good = hashlib.sha256(self.hbloader).hexdigest()
        self.assertEqual(self.run_tool("--expect-sha256", "0" * 64), 2)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.run_tool("--expect-sha256", good.upper()), 0)

    def test_hen_log_mutants_refuse(self):
        # A failed file absent from the folder, a truncated listing, another title: each refuses.
        mutants = (
            self.lines + ["capture file name=subsdk2 rc=0x2ee202 type=0x1 size=0x0 written=0x0 fnv64=0x0",
                          self.done.replace("failed=0x0", "failed=0x1")],
            [l.replace("entries=0x6", "entries=0x1a") for l in self.lines] + [self.done],
            [l.replace("100123400abc000", "100aaaa00000000") for l in self.lines + [self.done]],
            [l for l in self.lines if "name=sdk " not in l] + [self.done],
        )
        for bad in mutants:
            self.write_log(bad)
            self.assertEqual(self.run_tool("--force"), 2, bad)
        self.assertFalse(os.path.exists(self.out))

    def test_flipped_byte_refuses(self):
        with open(os.path.join(self.cap, "sdk"), "r+b") as f:
            f.seek(100)
            f.write(b"\xff")
        self.assertEqual(self.run_tool(), 2)

    def test_log_choice_is_explicit(self):
        self.assertEqual(quiet(mkdonor.main, [self.cap, "--hbloader", self.hbl]), 2)
        self.assertEqual(self.run_tool("--hen-log", self.log, "--no-hen-log"), 2)
        self.assertEqual(self.run_tool("--no-hen-log"), 0)

    def test_refusals(self):
        with self.assertRaises(SystemExit):  # --hbloader is required
            quiet(mkdonor.main, [self.cap, "--hen-log", self.log])
        self.assertEqual(self.run_tool("--tid", "0400123400abc000"), 2)  # a Switch 2 id
        self.assertEqual(self.run_tool("--tid", "0100000000000800"), 2)  # a system program id
        self.assertEqual(self.run_tool("--tid", "xyz"), 2)
        os.remove(os.path.join(self.cap, "main.npdm"))
        self.assertEqual(self.run_tool("--no-hen-log"), 2)  # no npdm

    def test_not_nso_refused(self):
        with open(os.path.join(self.cap, "subsdk0"), "wb") as f:
            f.write(b"XXXX" + b"\0" * 60)
        self.assertEqual(self.run_tool("--no-hen-log"), 2)
        with open(self.hbl, "wb") as f:
            f.write(b"\0" * 64)
        self.assertEqual(self.run_tool("--no-hen-log"), 2)

    def test_help(self):
        with self.assertRaises(SystemExit) as e:
            quiet(mkdonor.main, ["--help"])
        self.assertEqual(e.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
