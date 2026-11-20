#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""mkdonor.py - build a donor package for a Switch 1 game on a PC.

HEN with Switch 1 support builds /kaihen/bin/s1/default.nsp on the console by itself, so this is
optional: use it for a game that needs its own package. CAPTURE_DIR is a folder named after the
game's program ID (16 hex digits) holding that game's ExeFS (main, main.npdm, rtld, sdk,
subsdk0...), dumped from your own copy of the game. Run it with --no-hen-log.

The donor written is a PFS0 file <tid16>.nsp with
  rtld       = nx-hbloader (kaiHEN's game/main; pass the file or the kaiHEN release .zip)
  every other file of CAPTURE_DIR, unchanged (main, subsdk*, sdk, main.npdm, ...).
The game's own rtld is left out: hbloader takes its slot, so Loader runs hbloader first and
everything else it looks for is where it expects it. Put the result on the SD card as
/kaihen/bin/s1/<tid16>.nsp. The next R-held launch of that game opens hbmenu.

The layout follows kaiHEN's common/policy_package.cpp writeExeFsPackage, from two files to N:
  0x00  "PFS0" | u32 file count | u32 string table size | u32 0
  0x10  count x {u64 data offset, u64 size, u32 name offset, u32 0}   (24 bytes each)
        string table: the names, NUL-terminated, back to back, no padding
  data: each file at a 0x20-aligned offset from the data start, zero-filled gaps,
        the last file unpadded.
  Order: rtld, main, subsdk* (numeric), sdk, other files (sorted), main.npdm last. FS opens
  ExeFS files by name, so only the names matter.

--hen-log is only for ExeFS copies made by the earlier research HEN, which logged a
"capture file name=... size=... fnv64=..." line per file; it checks every file against it. The tool reads only
local files and writes only --out. The donor holds your game's code: keep it to yourself.
"""
import argparse
import hashlib
import os
import re
import struct
import sys
import zipfile

# nx-hbloader as shipped in kaiHEN release 20261117.1 (game/main), the one this was tested with.
TESTED_HBLOADER_SHA256 = "cd57628d91f11784085c858438fb392add20c6126bf7411c43d5a3dc83048874"
HBLOADER_IN_ZIP = "game/main"
FNV64_BASIS = 0xCBF29CE484222325
FNV64_PRIME = 0x100000001B3
NAME_LIMIT = 64  # HEN's capture name limit
TID_RE = re.compile(r"^[0-9a-f]{16}$")
S1_APPLICATION_MIN = 0x0100000000010000
S1_APPLICATION_MAX = 0x01FFFFFFFFFFFFFF


class DonorError(Exception):
    pass


def fnv64(data, h=FNV64_BASIS):
    """FNV-1a 64, the checksum HEN logs for each captured file."""
    for b in data:
        h ^= b
        h = (h * FNV64_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def align32(v):
    return (v + 0x1F) & ~0x1F


def is_capture_name(name):
    """A name HEN captures: one printable-ASCII path component, no separators, not . or .."""
    if not name or name in (".", "..") or len(name) > NAME_LIMIT:
        return False
    return all(0x20 < ord(c) < 0x7F and c not in "/\\:" for c in name)


def order_key(name):
    if name == "rtld":
        return (0, 0, name)
    if name == "main":
        return (1, 0, name)
    m = re.fullmatch(r"subsdk(\d+)", name)
    if m:
        return (2, int(m.group(1)), name)
    if name == "sdk":
        return (3, 0, name)
    if name == "main.npdm":
        return (5, 0, name)
    return (4, 0, name)


def build_pfs0(files):
    """files: [(name, bytes)] in package order. Returns the PFS0 bytes."""
    names = [n for n, _ in files]
    if len(set(names)) != len(names):
        raise DonorError("duplicate file name")
    table = b"".join(n.encode("ascii") + b"\0" for n in names)
    header = bytearray(b"PFS0" + struct.pack("<III", len(files), len(table), 0))
    data = bytearray()
    name_offset = 0
    for i, (name, blob) in enumerate(files):
        offset = align32(len(data)) if i else 0
        data += b"\0" * (offset - len(data))
        header += struct.pack("<QQII", offset, len(blob), name_offset, 0)
        data += blob
        name_offset += len(name) + 1
    return bytes(header) + table + bytes(data)


def parse_pfs0(blob):
    """Reads the layout above the way FS does (data start = 16 + 24*count + table size).
    Returns [(name, bytes)] in entry order; raises DonorError on anything out of bounds."""
    if len(blob) < 16 or blob[:4] != b"PFS0":
        raise DonorError("not PFS0")
    count, table_size, _ = struct.unpack_from("<III", blob, 4)
    entries_end = 16 + 24 * count
    data_start = entries_end + table_size
    if data_start > len(blob):
        raise DonorError("header past end")
    table = blob[entries_end:data_start]
    out = []
    for i in range(count):
        offset, size, name_offset, _ = struct.unpack_from("<QQII", blob, 16 + 24 * i)
        end = table.find(b"\0", name_offset)
        if name_offset >= len(table) or end < 0:
            raise DonorError("bad name offset")
        start = data_start + offset
        if start + size > len(blob):
            raise DonorError("file past end")
        out.append((table[name_offset:end].decode("ascii"), blob[start:start + size]))
    return out


CAPTURE_LINE = re.compile(r"^capture file name=(\S+) rc=0x([0-9a-f]+) type=0x[0-9a-f]+ size=0x([0-9a-f]+) "
                          r"written=0x([0-9a-f]+) fnv64=0x([0-9a-f]+)")
OPEN_LINE = re.compile(r"^capture open rc=0x([0-9a-f]+) program=0x([0-9a-f]+) ")
LIST_LINE = re.compile(r"^capture list rc=0x([0-9a-f]+) entries=0x([0-9a-f]+) read=0x([0-9a-f]+) ")
DONE_LINE = re.compile(r"^capture done program=0x([0-9a-f]+) files=0x([0-9a-f]+) failed=0x([0-9a-f]+) ")


def read_hen_log(path):
    """HEN's capture lines: {"files": {name: (rc, size, written, fnv64)}, "open": (rc, program),
    "list": (rc, entries, read), "done": (program, files, failed)}; the last line of each kind wins."""
    log = {"files": {}, "open": None, "list": None, "done": None}
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            line = line.strip()
            m = CAPTURE_LINE.match(line)
            if m:
                log["files"][m.group(1)] = tuple(int(g, 16) for g in m.groups()[1:])
            for key, rx in (("open", OPEN_LINE), ("list", LIST_LINE), ("done", DONE_LINE)):
                m = rx.match(line)
                if m:
                    log[key] = tuple(int(g, 16) for g in m.groups())
    return log


def check_capture_complete(log, tid):
    """Refuses a capture HEN did not finish: another title, a failed or truncated listing, a failed file."""
    want = int(tid, 16)
    if log["open"] is None or log["open"][0] != 0 or log["open"][1] != want:
        raise DonorError("HEN log: no 'capture open rc=0x0 program=0x%x' line (got %r)" % (want, log["open"]))
    if log["list"] is None or log["list"][0] != 0 or log["list"][1] != log["list"][2]:
        raise DonorError("HEN log: listing failed or truncated (capture list rc/entries/read = %r)" % (log["list"],))
    if log["done"] is None or log["done"][0] != want or log["done"][2] != 0:
        raise DonorError("HEN log: no 'capture done program=0x%x ... failed=0x0' line (got %r)" % (want, log["done"]))
    failed = sorted(n for n, v in log["files"].items() if v[0] != 0)
    if failed:
        raise DonorError("HEN log: capture failed for %s" % ", ".join(failed))
    if len(log["files"]) != log["list"][2] or log["done"][1] != log["list"][2]:
        raise DonorError("HEN log: %d file lines, %d listed, %d done" % (len(log["files"]), log["list"][2], log["done"][1]))


def collect(capture_dir, hbloader, hen_log=None, tid=None):
    """Returns (files in package order, report lines)."""
    if not os.path.isdir(capture_dir):
        raise DonorError("%s is not a directory" % capture_dir)
    title = {}
    for name in sorted(os.listdir(capture_dir)):
        path = os.path.join(capture_dir, name)
        if os.path.islink(path) or not os.path.isfile(path):
            raise DonorError("%s: not a regular file (the capture is flat)" % name)
        if not is_capture_name(name):
            raise DonorError("%s: not a name HEN would capture" % name)
        with open(path, "rb") as f:
            title[name] = f.read()
    for need in ("main", "main.npdm"):
        if need not in title:
            raise DonorError("capture has no %s" % need)
    if title["main.npdm"][:4] != b"META":
        raise DonorError("main.npdm is not META")
    for name, blob in title.items():
        if (name in ("main", "rtld", "sdk") or name.startswith("subsdk")) and blob[:4] != b"NSO0":
            raise DonorError("%s is not NSO0" % name)
    if hbloader[:4] != b"NSO0":
        raise DonorError("hbloader is not NSO0")
    report = []
    if hen_log is not None:
        check_capture_complete(hen_log, tid)
        lines = hen_log["files"]
        for name, blob in title.items():
            if name not in lines:
                raise DonorError("%s: no 'capture file' line in the HEN log" % name)
            rc, size, written, h = lines[name]
            if rc != 0 or size != len(blob) or written != size or fnv64(blob) != h:
                raise DonorError("%s: HEN log rc=%#x size=%#x written=%#x fnv64=%#x, local size=%#x fnv64=%#x"
                                 % (name, rc, size, written, h, len(blob), fnv64(blob)))
            report.append("verified %s size=%#x fnv64=%#x" % (name, size, h))
        missing = sorted(n for n, v in lines.items() if v[0] == 0 and n not in title)
        if missing:
            raise DonorError("HEN captured %s but the directory lacks them" % ", ".join(missing))
    files = [("rtld", hbloader)] + [(n, b) for n, b in title.items() if n != "rtld"]
    files.sort(key=lambda nb: order_key(nb[0]))
    if "rtld" in title:
        report.append("title rtld (%d bytes) replaced by hbloader" % len(title["rtld"]))
    else:
        report.append("title has no rtld; hbloader added as rtld")
    return files, report


def read_hbloader(path):
    """The hbloader bytes, from the file itself or from game/main inside a kaiHEN release .zip."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            try:
                return z.read(HBLOADER_IN_ZIP)
            except KeyError:
                raise DonorError("%s has no %s (is it a kaiHEN release?)" % (path, HBLOADER_IN_ZIP))
    with open(path, "rb") as f:
        return f.read()


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="mkdonor.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture_dir", metavar="CAPTURE_DIR",
                    help="folder named after the program ID, holding the game's ExeFS")
    ap.add_argument("--hbloader", required=True, metavar="FILE_OR_ZIP",
                    help="nx-hbloader: kaiHEN's game/main, or the kaiHEN release .zip that contains it")
    ap.add_argument("--expect-sha256", metavar="SHA256",
                    help="refuse unless the hbloader has this sha256 (default: only report it)")
    ap.add_argument("--hen-log", metavar="HEN_LOG",
                    help="research HEN capture log to check the files against (older builds only)")
    ap.add_argument("--no-hen-log", action="store_true",
                    help="no capture log (the usual case)")
    ap.add_argument("--tid", metavar="TID16",
                    help="title id, 16 hex digits (default: the name of CAPTURE_DIR)")
    ap.add_argument("--out", metavar="FILE",
                    help="output file (default: <tid16>.nsp next to CAPTURE_DIR)")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    try:
        capture_dir = os.path.abspath(a.capture_dir)
        tid = (a.tid or os.path.basename(capture_dir.rstrip(os.sep))).lower()
        if not TID_RE.match(tid):
            raise DonorError("title id %r is not 16 hex digits (name the folder after it or pass --tid)" % tid)
        if not S1_APPLICATION_MIN <= int(tid, 16) <= S1_APPLICATION_MAX:
            raise DonorError("%s is not a Switch 1 application id" % tid)
        if bool(a.hen_log) == bool(a.no_hen_log):
            raise DonorError("pass --hen-log HEN_LOG (or, to skip the check, --no-hen-log)")
        hbloader = read_hbloader(a.hbloader)
        hb_sha = hashlib.sha256(hbloader).hexdigest()
        if a.expect_sha256 and hb_sha != a.expect_sha256.lower():
            raise DonorError("hbloader sha256 %s != expected %s" % (hb_sha, a.expect_sha256))
        hen_log = read_hen_log(a.hen_log) if a.hen_log else None
        files, report = collect(capture_dir, hbloader, hen_log, tid)
        package = build_pfs0(files)
        if parse_pfs0(package) != files:
            raise DonorError("round trip failed")
        out = a.out or os.path.join(os.path.dirname(capture_dir), tid + ".nsp")
        if os.path.exists(out) and not a.force:
            raise DonorError("%s exists (use --force)" % out)
        with open(out, "wb") as f:
            f.write(package)
    except (DonorError, OSError, zipfile.BadZipFile) as e:
        print("mkdonor: refused: %s" % e, file=sys.stderr)
        return 2
    print("hbloader sha256 %s%s" % (hb_sha, "" if hb_sha == TESTED_HBLOADER_SHA256
                                    else "  (not the tested kaiHEN 20261117.1 build)"))
    if hen_log is None:
        print("warning: built without the HEN log check")
    for line in report:
        print(line)
    for name, blob in files:
        print("  %-10s %10d  sha256 %s%s" % (name, len(blob), hashlib.sha256(blob).hexdigest(),
                                           "  (hbloader)" if name == "rtld" else ""))
    print("wrote %s  %d bytes  %d files  sha256 %s" % (out, len(package), len(files),
                                                       hashlib.sha256(package).hexdigest()))
    print("copy it to the SD card as /kaihen/bin/s1/%s.nsp" % tid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
