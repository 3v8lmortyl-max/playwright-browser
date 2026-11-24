#!/usr/bin/env python3
"""T11 — original artifact timestamp and content provenance (unprivileged orchestrator).

Uses the ORIGINAL GitHub Actions artifact `agent-framework-pypi-documentary`
(artifact 14799922102 from run 45136043351) and its original member
`screenshots/01_github.com_01.png`. The archive is downloaded through the
Actions artifact REST endpoint with GITHUB_TOKEN (actions: read), verified by
SHA-256 before any test, and the member is verified by SHA-256, size and CRC32.
If the artifact is unavailable, expired or mismatched, the original-artifact
tests are stopped and the failure is reported; there is no silent fallback.

`--self-test` exists only to validate the harness where the artifact cannot be
fetched (for example, a sandbox whose proxy blocks Azure blob storage). It uses a
stand-in archive, and every output it produces is labelled SELF_TEST and must not
be read as provenance evidence.

Nothing here accesses or claims access to the historical ChatGPT filesystem.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
WRAPPER = str(HERE / "t11_privileged_wrapper.sh")

REPO = "3v8lmortyl-max/playwright-browser"
RUN_ID = 45136043351
ARTIFACT_ID = 14799922102
ARTIFACT_NAME = "agent-framework-pypi-documentary"
MEMBER = "screenshots/01_github.com_01.png"
EXPECTED = {
    "zip_sha256": "cafc11da650cb8d2be6564eadef1830e8f9b5cfd12541d24b85dd038676b2f65",
    "png_sha256": "dd0ff1aa6371838a1bd9d941b4b43a1596a01974c202a6967416f20916f61f51",
    "png_size": 209306,
    "png_mode": "0100644",
    "png_compressed_size": 198259,
    "png_crc32": "0e9614e3",
}
HISTORICAL = {
    "path": "/mnt/data/gh_demo/screenshots/01_github.com_01.png",
    "uid": 0, "gid": 1001, "mode": "0644", "size": 209306,
    "sha256": EXPECTED["png_sha256"], "device": 27, "inode": 262311, "mount_id": 52,
    "parent": {"path": "/mnt/data/gh_demo", "uid": 0, "gid": 1001, "mode": "2755"},
    "mtime": None, "ctime": None, "atime": None, "birth": None,
    "timestamp_note": "historical mtime/ctime have not been established in the available evidence",
}

SIG_EOCD, SIG_Z64LOC, SIG_Z64EOCD = b"PK\x05\x06", b"PK\x06\x07", b"PK\x06\x06"
SIG_CD, SIG_LFH, SIG_DD = b"PK\x01\x02", b"PK\x03\x04", b"PK\x07\x08"
HOST_OS = {0: "MS-DOS / FAT", 1: "Amiga", 2: "OpenVMS", 3: "Unix", 6: "OS/2 HPFS", 7: "Macintosh",
           10: "Windows NTFS", 11: "MVS", 14: "VFAT", 19: "OS X (Darwin)"}
METHODS = {0: "stored", 8: "deflated", 9: "deflate64", 12: "bzip2", 14: "LZMA", 93: "Zstandard"}
EXTRA_NAMES = {0x0001: "Zip64 extended information", 0x000A: "NTFS timestamps",
               0x000D: "PKWARE Unix (uid/gid)", 0x5455: "Extended timestamp (UT)",
               0x5855: "Info-ZIP Unix v1 (UX, uid/gid)", 0x7855: "Info-ZIP Unix v2 (Ux, uid/gid)",
               0x7875: "Info-ZIP Unix v3 (ux, uid/gid)", 0x7075: "Info-ZIP Unicode path",
               0x6375: "Info-ZIP Unicode comment", 0xCAFE: "JAR marker"}
OWNERSHIP_EXTRAS = {0x000D, 0x5855, 0x7855, 0x7875}


def utc(epoch_s):
    return datetime.datetime.fromtimestamp(epoch_s, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Acquisition
# --------------------------------------------------------------------------- #
def _api(url, token):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28",
                                               "User-Agent": "t11-timestamp-provenance"})
    # Unredirected: the token is never forwarded to the blob-storage redirect target.
    req.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=120)


def download_artifact(dest: Path) -> dict:
    rec = {"method": "GitHub Actions artifact REST API", "repository": REPO, "artifact_id": ARTIFACT_ID,
           "endpoint": f"GET /repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip", "ok": False}
    token = os.environ.pop("GITHUB_TOKEN", None) or os.environ.pop("GH_TOKEN", None)
    if not token:
        rec["error"] = "GITHUB_TOKEN not available"
        return rec
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    try:
        with _api(f"{api}/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}", token) as r:
            meta = json.loads(r.read())
        rec["api_metadata"] = {k: meta.get(k) for k in ("id", "name", "size_in_bytes", "expired", "created_at",
                                                         "updated_at", "expires_at", "digest")}
        rec["api_metadata"]["workflow_run_id"] = (meta.get("workflow_run") or {}).get("id")
        rec["api_metadata"]["workflow_run_head_sha"] = (meta.get("workflow_run") or {}).get("head_sha")
        problems = []
        if meta.get("name") != ARTIFACT_NAME:
            problems.append(f"name {meta.get('name')!r} != {ARTIFACT_NAME!r}")
        if meta.get("expired"):
            problems.append("artifact is expired")
        if rec["api_metadata"]["workflow_run_id"] != RUN_ID:
            problems.append(f"workflow run {rec['api_metadata']['workflow_run_id']} != {RUN_ID}")
        if problems:
            rec["error"] = "; ".join(problems)
            return rec
        t0 = time.time()
        h = hashlib.sha256()
        n = 0
        with _api(f"{api}/repos/{REPO}/actions/artifacts/{ARTIFACT_ID}/zip", token) as r, open(dest, "wb") as out:
            rec["http_status"] = r.status
            final = urllib.parse.urlsplit(r.geturl())
            rec["final_host"] = final.netloc  # query string (signature) deliberately not recorded
            while chunk := r.read(1 << 20):
                h.update(chunk)
                out.write(chunk)
                n += len(chunk)
        rec.update({"ok": True, "bytes": n, "sha256": h.hexdigest(), "seconds": round(time.time() - t0, 3),
                    "downloaded_utc": utc(time.time())})
    except urllib.error.HTTPError as exc:
        rec["error"] = f"HTTP {exc.code} {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        token = None  # noqa: F841
    return rec


def build_selftest_zip(dest: Path) -> dict:
    """Harness validation only. Clearly labelled; never used as provenance evidence."""
    png = b"\x89PNG\r\n\x1a\n" + hashlib.sha256(b"t11-self-test").digest() * 64
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo(MEMBER, date_time=(2026, 11, 23, 8, 8, 40))
        zi.create_system, zi.external_attr, zi.compress_type = 3, 0o100644 << 16, zipfile.ZIP_DEFLATED
        zf.writestr(zi, png)
        other = zipfile.ZipInfo("output/unrelated.bin", date_time=(2026, 11, 23, 8, 8, 40))
        other.create_system, other.external_attr = 3, 0o100644 << 16
        zf.writestr(other, b"unrelated entry that must never be extracted\n")
    return {"method": "SELF_TEST stand-in archive (NOT the original artifact)", "ok": True,
            "sha256": sha256_path(dest), "bytes": dest.stat().st_size,
            "png_sha256": hashlib.sha256(png).hexdigest(), "png_size": len(png)}


# --------------------------------------------------------------------------- #
# ZIP binary inspection
# --------------------------------------------------------------------------- #
def decode_dos(t: int, d: int) -> dict:
    y, mo, dd = (d >> 9) + 1980, (d >> 5) & 0xF, d & 0x1F
    hh, mi, ss = t >> 11, (t >> 5) & 0x3F, (t & 0x1F) * 2
    out = {"raw_time": f"0x{t:04x}", "raw_date": f"0x{d:04x}",
           "fields": {"year": y, "month": mo, "day": dd, "hour": hh, "minute": mi, "second": ss},
           "naive_local": f"{y:04d}-{mo:02d}-{dd:02d}T{hh:02d}:{mi:02d}:{ss:02d}",
           "resolution_seconds": 2, "subsecond": "not representable", "timezone": "not recorded"}
    try:
        out["if_producer_tz_was_utc"] = datetime.datetime(y, mo, dd, hh, mi, ss,
                                                          tzinfo=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out["if_producer_tz_was_utc_epoch"] = int(datetime.datetime(y, mo, dd, hh, mi, ss,
                                                                    tzinfo=datetime.timezone.utc).timestamp())
    except ValueError:
        out["if_producer_tz_was_utc"] = None
    return out


def _filetime(v):
    return (v - 116444736000000000) / 1e7


def decode_extra(raw: bytes, where: str) -> list:
    out, i = [], 0
    while i + 4 <= len(raw):
        hid, ln = struct.unpack_from("<HH", raw, i)
        body = raw[i + 4:i + 4 + ln]
        i += 4 + ln
        f = {"id": f"0x{hid:04x}", "name": EXTRA_NAMES.get(hid, "unknown/vendor"), "length": ln,
             "hex": body.hex()[:256], "carries_ownership": hid in OWNERSHIP_EXTRAS}
        try:
            if hid == 0x5455 and body:
                flags = body[0]
                f["flags"] = flags
                j = 1
                for bit, label in ((1, "mtime"), (2, "atime"), (4, "ctime")):
                    if flags & bit and j + 4 <= len(body):
                        v = struct.unpack_from("<i", body, j)[0]
                        f[f"{label}_epoch"], f[f"{label}_utc"] = v, utc(v)
                        j += 4
                f["note"] = (f"{where}: UT stores UTC seconds (1-s resolution); the central-directory copy "
                             "normally carries only mtime")
            elif hid == 0x000A and len(body) >= 4:
                j = 4
                while j + 4 <= len(body):
                    tag, sz = struct.unpack_from("<HH", body, j)
                    if tag == 1 and sz >= 24:
                        m, a, c = struct.unpack_from("<QQQ", body, j + 4)
                        f.update({"mtime_utc": utc(_filetime(m)), "atime_utc": utc(_filetime(a)),
                                  "ctime_utc": utc(_filetime(c)), "resolution": "100 ns"})
                    j += 4 + sz
            elif hid == 0x7875 and len(body) >= 3:
                us = body[1]
                gs = body[2 + us]
                f["uid"] = int.from_bytes(body[2:2 + us], "little")
                f["gid"] = int.from_bytes(body[3 + us:3 + us + gs], "little")
            elif hid == 0x0001:
                f["values"] = [struct.unpack_from("<Q", body, k)[0] for k in range(0, len(body) - 7, 8)]
        except (struct.error, IndexError) as exc:
            f["decode_error"] = str(exc)
        out.append(f)
    return out


def _flags(fl: int) -> dict:
    return {"value": f"0x{fl:04x}", "encrypted": bool(fl & 1), "data_descriptor": bool(fl & 8),
            "utf8_names": bool(fl & 0x800)}


def _ext_attrs(ext: int, host: int) -> dict:
    d = {"raw": f"0x{ext:08x}", "msdos_attributes": f"0x{ext & 0xFF:02x}"}
    if host in (3, 19):
        d["unix_mode"] = format(ext >> 16, "07o")
    return d


def parse_zip(path: Path, member: str) -> dict:
    data = path.read_bytes()
    pos = data.rfind(SIG_EOCD, max(0, len(data) - 65557))
    if pos < 0:
        raise ValueError("End of Central Directory record not found")
    (_, disk, cd_disk, n_disk, n_total, cd_size, cd_off, clen) = struct.unpack_from("<4sHHHHIIH", data, pos)
    eocd = {"offset": pos, "disk": disk, "entries_total": n_total, "cd_size": cd_size, "cd_offset": cd_off,
            "comment_length": clen}
    zip64 = None
    loc = pos - 20
    if loc >= 0 and data[loc:loc + 4] == SIG_Z64LOC:
        z64_off = struct.unpack_from("<Q", data, loc + 8)[0]
        if data[z64_off:z64_off + 4] == SIG_Z64EOCD:
            (_, _sz, vmade, vneed, _d, _cd, n_d, n_t, cds, cdo) = struct.unpack_from("<4sQHHIIQQQQ", data, z64_off)
            zip64 = {"offset": z64_off, "entries_total": n_t, "cd_size": cds, "cd_offset": cdo}
            n_total, cd_off = n_t, cdo

    entries, member_cd = [], None
    p = cd_off
    for _ in range(n_total):
        if data[p:p + 4] != SIG_CD:
            raise ValueError(f"bad central directory signature at {p}")
        (_, vmade, vneed, fl, meth, mt, md, crc, cs, us, nl, xl, cl, dstart, iattr, eattr,
         lfh) = struct.unpack_from("<4sHHHHHHIIIHHHHHII", data, p)
        name_b = data[p + 46:p + 46 + nl]
        extra = data[p + 46 + nl:p + 46 + nl + xl]
        name = name_b.decode("utf-8" if fl & 0x800 else "cp437", "replace")
        ex = decode_extra(extra, "central directory")
        z64 = next((e for e in ex if e["id"] == "0x0001"), None)
        if z64 and z64.get("values"):
            vals = list(z64["values"])
            if us == 0xFFFFFFFF and vals:
                us = vals.pop(0)
            if cs == 0xFFFFFFFF and vals:
                cs = vals.pop(0)
            if lfh == 0xFFFFFFFF and vals:
                lfh = vals.pop(0)
        host = vmade >> 8
        e = {"name": name, "method": METHODS.get(meth, meth), "compressed_size": cs, "uncompressed_size": us,
             "crc32": f"{crc:08x}", "dos": decode_dos(mt, md), "extra_field_ids": [x["id"] for x in ex]}
        entries.append(e)
        if name == member:
            member_cd = {
                "offset": p, "version_made_by": {"raw": vmade, "host_os": HOST_OS.get(host, f"unknown ({host})"),
                                                 "host_os_code": host, "spec_version": f"{(vmade & 0xFF) / 10:.1f}"},
                "version_needed": f"{vneed / 10:.1f}", "general_purpose_flags": _flags(fl),
                "compression_method": {"code": meth, "name": METHODS.get(meth, "unknown")},
                "dos_modification": decode_dos(mt, md), "crc32": f"{crc:08x}",
                "compressed_size": cs, "uncompressed_size": us, "name_length": nl, "extra_length": xl,
                "comment_length": cl, "disk_number_start": dstart, "internal_attributes": f"0x{iattr:04x}",
                "external_attributes": _ext_attrs(eattr, host), "local_header_offset": lfh,
                "extra_fields": ex, "name_raw_hex": name_b.hex(),
            }
        p += 46 + nl + xl + cl

    if member_cd is None:
        raise KeyError(f"member {member!r} not in central directory")

    lo = member_cd["local_header_offset"]
    if data[lo:lo + 4] != SIG_LFH:
        raise ValueError("bad local file header signature")
    (_, vneed, fl, meth, mt, md, crc, cs, us, nl, xl) = struct.unpack_from("<4sHHHHHIIIHH", data, lo)
    lname = data[lo + 30:lo + 30 + nl]
    lextra = data[lo + 30 + nl:lo + 30 + nl + xl]
    data_start = lo + 30 + nl + xl
    lfh = {"offset": lo, "version_needed": f"{vneed / 10:.1f}", "general_purpose_flags": _flags(fl),
           "compression_method": {"code": meth, "name": METHODS.get(meth, "unknown")},
           "dos_modification": decode_dos(mt, md), "crc32_field": f"{crc:08x}", "compressed_size_field": cs,
           "uncompressed_size_field": us, "name": lname.decode("utf-8", "replace"),
           "name_matches_central_directory": lname.hex() == member_cd["name_raw_hex"],
           "extra_fields": decode_extra(lextra, "local header"), "data_offset": data_start}
    dd = None
    if fl & 8:
        q = data_start + member_cd["compressed_size"]
        has_sig = data[q:q + 4] == SIG_DD
        q2 = q + (4 if has_sig else 0)
        is64 = any(x["id"] == "0x0001" for x in lfh["extra_fields"])
        if is64:
            dcrc, dcs, dus = struct.unpack_from("<IQQ", data, q2)
        else:
            dcrc, dcs, dus = struct.unpack_from("<III", data, q2)
        dd = {"offset": q, "signature_present": has_sig, "crc32": f"{dcrc:08x}", "compressed_size": dcs,
              "uncompressed_size": dus, "zip64": is64}
    lfh["data_descriptor"] = dd
    lfh["dos_matches_central_directory"] = (lfh["dos_modification"]["raw_time"] ==
                                            member_cd["dos_modification"]["raw_time"] and
                                            lfh["dos_modification"]["raw_date"] ==
                                            member_cd["dos_modification"]["raw_date"])

    timestamp_fields = [x for x in member_cd["extra_fields"] + lfh["extra_fields"]
                        if x["id"] in ("0x5455", "0x000a")]
    ownership_fields = [x for x in member_cd["extra_fields"] + lfh["extra_fields"] if x["carries_ownership"]]
    return {"archive_size": len(data), "eocd": eocd, "zip64_eocd": zip64, "entry_count": len(entries),
            "entries_metadata_only": entries, "member": member, "central_directory": member_cd,
            "local_file_header": lfh, "extended_timestamp_fields": timestamp_fields,
            "ownership_extra_fields": ownership_fields}


def verify_member(path: Path, member: str, manifest: dict, expect: dict) -> dict:
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(member)
        data = zf.read(info)  # zipfile verifies CRC32 while reading
    cd = manifest["central_directory"]
    checks = {
        "sha256": [hashlib.sha256(data).hexdigest(), expect["png_sha256"]],
        "size": [len(data), expect["png_size"]],
        "crc32_computed": [f"{zlib.crc32(data) & 0xFFFFFFFF:08x}", expect["png_crc32"]],
        "crc32_central_directory": [cd["crc32"], expect["png_crc32"]],
        "compressed_size": [cd["compressed_size"], expect["png_compressed_size"]],
        "unix_mode": ["0" + cd["external_attributes"].get("unix_mode", "")[-6:], expect["png_mode"]],
        "no_ownership_extra_fields": [not manifest["ownership_extra_fields"], True],
    }
    result = {k: {"observed": v[0], "expected": v[1], "pass": v[0] == v[1]} for k, v in checks.items()}
    return {"member": member, "png_signature_ok": data[:8] == b"\x89PNG\r\n\x1a\n", "checks": result,
            "all_pass": all(c["pass"] for c in result.values())}


# --------------------------------------------------------------------------- #
# Historical evidence search, filesystem info
# --------------------------------------------------------------------------- #
def search_historical() -> dict:
    pats = [re.compile(r"262311"), re.compile(r"/mnt/data/gh_demo"), re.compile(r"01_github\.com_01\.png")]
    tsre = re.compile(r"(mtime|ctime|atime|birth|Modify|Change|Birth)\W{0,4}[:=]?\s*\"?\d{4}-\d\d-\d\d|"
                      r"(st_mtime|st_ctime)\w*\W{0,4}\d{9,}")
    hits = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__") and not d.startswith("evidence")]
        for f in files:
            fp = Path(root) / f
            try:
                if fp.stat().st_size > 2_000_000:
                    continue
                text = fp.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = str(fp.relative_to(REPO_ROOT))
            self_ref = rel.startswith("security/archive-boundary/") or rel.startswith(".github/workflows/archive-boundary")
            for n, line in enumerate(text.splitlines(), 1):
                if any(p.search(line) for p in pats):
                    hits.append({"file": rel, "line": n, "text": line.strip()[:200], "investigation_code": self_ref,
                                 "contains_timestamp_value": bool(tsre.search(line))})
    historical_ts = [h for h in hits if h["contains_timestamp_value"] and not h["investigation_code"]]
    return {"searched": str(REPO_ROOT.name), "patterns": [p.pattern for p in pats], "hits": hits[:200],
            "historical_timestamp_values_found": bool(historical_ts), "historical_timestamp_hits": historical_ts,
            "conclusion": ("no historical mtime/ctime values for the PNG were found in the repository"
                           if not historical_ts else "candidate historical timestamp values found; see hits")}


def filesystem_info(path: Path) -> dict:
    st = os.stat(path)
    info = {"path": str(path), "st_dev": st.st_dev, "dev_major_minor": f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}",
            "kernel": os.uname().release}
    for key, cmd in (("statfs", ["stat", "-f", "-c", "type=%T blocksize=%S", str(path)]),
                     ("findmnt", ["findmnt", "-J", "-T", str(path)])):
        try:
            info[key] = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception as exc:  # noqa: BLE001
            info[key] = f"unavailable: {exc}"
    try:
        mm = f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
        best = None
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            f = line.split()
            if len(f) > 4 and f[2] == mm and str(path).startswith(f[4]):
                if best is None or len(f[4]) > len(best[4]):
                    best = f
        if best:
            sep = best.index("-")
            info["mountinfo"] = {"mount_id": int(best[0]), "parent_id": int(best[1]), "major_minor": best[2],
                                 "mount_point": best[4], "mount_options": best[5], "fstype": best[sep + 1],
                                 "source": best[sep + 2], "super_options": best[sep + 3] if len(best) > sep + 3 else ""}
    except OSError as exc:
        info["mountinfo"] = f"unavailable: {exc}"
    hist_dev = HISTORICAL["device"]
    info["historical_device_decoded"] = {
        "st_dev": hist_dev, "major": os.major(hist_dev), "minor": os.minor(hist_dev),
        "interpretation": ("major 0 is the anonymous (unnamed) device class the kernel assigns to filesystems "
                           "without a backing block device — e.g. tmpfs, overlayfs, FUSE, 9p, virtiofs, NFS. "
                           "It excludes a directly mounted disk partition but does not identify which type."),
        "assumption": "the recorded device value 27 is a decimal st_dev as returned by stat(2)"}
    return info


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _mtime_kind(src: str) -> str:
    if not src:
        return "unknown"
    if src.startswith("archive DOS"):
        return "archive DOS timestamp (" + src.split("TZ=")[-1] + ")"
    if src.startswith("archive extended"):
        return "archive UT timestamp"
    if src.startswith("source file mtime"):
        return "preserved from source (ns-exact)"
    if src.startswith("time of step"):
        label = src.split("(", 1)[1].rsplit(")", 1)[0]
        return f"new — time of operation ({label})"
    return src


def _ts_syscall_kind(lines):
    kinds = set()
    for l in lines:
        if "UTIME_NOW" in l:
            kinds.add("utimensat(UTIME_NOW)")
        elif "[{tv_sec" in l:
            kinds.add("utimensat(explicit times)")
        elif "NULL, 0)" in l:
            kinds.add("utimensat(NULL → now)")
        elif "futimesat" in l or "utime" in l:
            kinds.add("other time-setting call")
    return sorted(kinds)


def _chown_kinds(lines):
    return sorted({m.group(1) for l in lines for m in [re.search(r"\b(f?chown(?:at)?|lchown)\(", l)] if m})


def build_report(results, manifest, verification, acquisition, fsinfo, search, selftest):
    scen = results["scenarios"]
    by_op = {}
    for s in scen:
        by_op.setdefault(s["operation"], []).append(s)

    ops = []
    for op_id, items in by_op.items():
        ok = [s for s in items if s["evaluation"]["content_sha256_ok"]
              and all(st["returncode"] == 0 for st in s["steps"] if st["role"] == "op")]
        rep = next((s for s in ok if s["profile"] == "A" and s["identity"] == "root"), ok[0] if ok else None)
        reproduced = [f"{s['profile']}/{s['identity']}" for s in items if s["evaluation"]["classification"] == "REPRODUCED"]
        row = {"operation": op_id, "group": items[0]["group"], "title": items[0]["title"],
               "successful_runs": len(ok), "runs": len(items), "reproduced_in": reproduced,
               "verdict": ("CONSISTENT" if reproduced else
                           "INCONCLUSIVE" if all(s["evaluation"]["classification"] == "INCONCLUSIVE" for s in items)
                           else "RULED_OUT"),
               "mtime_behaviour": sorted({_mtime_kind(s["evaluation"]["timestamps"].get("mtime_source")) for s in ok}),
               "ownership_syscalls": sorted({k for s in items for k in _chown_kinds(s["evaluation"]["ownership_syscalls_in_operation"])}),
               "timestamp_syscalls": sorted({k for s in items for k in _ts_syscall_kind(s["evaluation"]["timestamp_syscalls_in_operation"])}),
               "ruled_out_reasons": sorted({s["evaluation"]["reason"] for s in items
                                            if s["evaluation"]["classification"] == "RULED_OUT"})}
        if rep:
            tm, t = rep["evaluation"]["target"], rep["evaluation"]["timestamps"]
            row.update({"example_config": f"{rep['profile']}/{rep['identity']}",
                        "example_owner_mode": f"{tm['uid']}:{tm['gid']} {tm['perm_octal']}",
                        "example_mtime_utc": tm["mtime_utc"], "example_ctime_utc": tm["ctime_utc"],
                        "example_birth": tm.get("birth_human"),
                        "mtime_subsecond_ns": t.get("mtime_subsecond_ns"),
                        "ctime_set_by": t.get("ctime_set_by_step"), "ctime_after_mtime": t.get("ctime_after_mtime"),
                        "rename_preserved_inode": t.get("rename_preserved_inode"),
                        "rename_preserved_mtime": t.get("rename_preserved_mtime"),
                        "rename_changed_ctime": t.get("rename_changed_ctime"),
                        "temp_owner_before_rename": t.get("temp_owner_before_rename"),
                        "replaced_pre_existing_inode": t.get("replaced_inode")})
        ops.append(row)

    cls_count = {}
    for s in scen:
        c = s["evaluation"]["classification"]
        cls_count[c] = cls_count.get(c, 0) + 1
    unrelated = [s["id"] for s in scen
                 if any(k.startswith("dest/") and not (k in ("dest/screenshots", f"dest/{MEMBER}") or k.endswith(".tmp"))
                        for k in (s["steps"][-1]["snapshot_after"] if s["steps"] else {}))]

    cd = manifest["central_directory"]
    dos = cd["dos_modification"]
    created = (acquisition.get("api_metadata") or {}).get("created_at")
    dos_vs_created = None
    if created and dos.get("if_producer_tz_was_utc_epoch"):
        c_epoch = datetime.datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
        dos_vs_created = int(dos["if_producer_tz_was_utc_epoch"] - c_epoch)

    ut = manifest["extended_timestamp_fields"]
    zip_proves = [
        f"Archive SHA-256 {acquisition.get('sha256')} {'matches' if acquisition.get('sha256') == EXPECTED['zip_sha256'] else 'DOES NOT match'} "
        f"the expected {EXPECTED['zip_sha256']}.",
        f"Member `{MEMBER}` decompresses to SHA-256 {verification['checks']['sha256']['observed']}, "
        f"{verification['checks']['size']['observed']} bytes, CRC32 {verification['checks']['crc32_computed']['observed']} "
        f"({'all member checks pass' if verification['all_pass'] else 'SOME MEMBER CHECKS FAIL'}).",
        f"Creator: version-made-by host OS {cd['version_made_by']['host_os']} (spec {cd['version_made_by']['spec_version']}), "
        f"external attributes {cd['external_attributes']['raw']} → Unix mode {cd['external_attributes'].get('unix_mode')}; "
        f"method {cd['compression_method']['name']}; data descriptor {cd['general_purpose_flags']['data_descriptor']}.",
        f"Ownership: {'no' if not manifest['ownership_extra_fields'] else 'PRESENT'} UID/GID extra fields "
        f"(extra field IDs: CD {cd['extra_fields'] and [x['id'] for x in cd['extra_fields']] or 'none'}, "
        f"LFH {[x['id'] for x in manifest['local_file_header']['extra_fields']] or 'none'}). The archive therefore "
        "cannot be the source of the file's UID 0 or GID 1001.",
        f"Timestamp: the only modification time is the MS-DOS field {dos['naive_local']} (raw date {dos['raw_date']}, "
        f"time {dos['raw_time']}): local wall-clock time with no zone, 2-second resolution, no sub-second part. "
        f"Extended timestamp fields: {'present' if ut else 'none'}. No ctime, atime or birth time is recorded.",
        (f"If the producer ran in UTC (GitHub-hosted runners default to UTC), the DOS time is {dos.get('if_producer_tz_was_utc')}, "
         f"{dos_vs_created:+d} s relative to the artifact's API created_at {created}." if dos_vs_created is not None
         else "Artifact creation time not available for comparison."),
        "Sufficiency: the ZIP can explain a historical mtime only if that mtime equals the DOS time interpreted in "
        "the extractor's time zone with zero nanoseconds (the behaviour of an mtime-restoring extractor such as Info-ZIP "
        "unzip). It cannot explain any ctime: ctime is set by the kernel at inode change and cannot be restored by "
        "ordinary timestamp-preservation operations.",
    ]

    experiments = [f"{o['operation']} ({o['title']}): REPRODUCED in {', '.join(o['reproduced_in'])}; mtime "
                   f"{'; '.join(o['mtime_behaviour'])}" for o in ops if o["reproduced_in"]]

    repro_cfg = sorted({f"{s['profile']}/{s['identity']}" for s in scen
                        if s["evaluation"]["classification"] == "REPRODUCED"})
    hist_supports = [
        f"UID 0, GID 1001, mode 0644, size {HISTORICAL['size']} and the SHA-256 together with the parent state are "
        f"reproduced only in configuration(s) {', '.join(repro_cfg) or 'none'} (profile/identity).",
        f"The historical SHA-256 equals the verified member SHA-256, so the historical content is byte-identical to "
        f"`{MEMBER}` in artifact {ARTIFACT_ID}. This establishes content identity only; hashes cannot show how the "
        "bytes were transferred.",
        f"Parent {HISTORICAL['parent']['path']} 0:1001 2755 matches profile A only; profiles B (1000:1001) and C "
        "(1000:1000 0755) conflict.",
        "Device 27 decodes to major 0 / minor 27, an anonymous device class used by virtual or network filesystems "
        "(tmpfs, overlayfs, FUSE, 9p, virtiofs, NFS), not a disk partition. Inode 262311 and mount ID 52 are "
        "meaningful only inside that boot and mount namespace and cannot be reproduced or compared here.",
        f"Historical timestamps: {search['conclusion']}. Every timestamp dimension is therefore INCONCLUSIVE.",
    ]

    def _uniq(reasons):
        seen = []
        for r in reasons:
            for frag in r.split("; "):
                if frag not in seen:
                    seen.append(frag)
        return "; ".join(seen)

    eliminated = [f"{o['operation']} ({o['title']}): no configuration reproduced — {_uniq(o['ruled_out_reasons'])}"
                  for o in ops if o["verdict"] == "RULED_OUT"]
    repro = [s for s in scen if s["evaluation"]["classification"] == "REPRODUCED"]
    if not any(s["identity"] == "unpriv" for s in repro):
        eliminated.append("Unprivileged execution (uid 1000) under every tested operation: no run reproduced the "
                          "historical attributes (each was either denied or produced file UID 1000).")
    for prof in ("A", "B", "C"):
        if not any(s["profile"] == prof for s in repro):
            spec = results["profiles"][prof]
            eliminated.append(f"Destination profile {prof} ({spec['owner']}:{spec['group']} {spec['mode']}) under "
                              "every tested operation: no run reproduced the historical file and parent state.")
    if not manifest["ownership_extra_fields"]:
        eliminated.append("Archive-borne ownership (for example `unzip -X`): the original ZIP contains no UID/GID "
                          "extra fields.")

    unknowns = [
        "Historical mtime, ctime, atime and birth time of the PNG, screenshots/ and gh_demo. These are needed to choose "
        "between mtime-restoring extraction (mtime = DOS time) and new-mtime operations (zipfile, cp, streaming, "
        "atomic replace).",
        "The historical extractor's time zone, which determines how the zone-less DOS time would have been applied "
        "(demonstrated by 01tz_unzip_alt_tz).",
        "Which binary or library performed the extraction or copy, with what arguments and credentials (process and "
        "credential audit telemetry).",
        "Whether any later rename, replace, chown, chmod or touch changed the inode (inode/ctime history).",
        "The filesystem type behind device 0:27 / mount 52, its mount options (grpid, idmapping, FUSE ownership "
        "translation) and namespace UID/GID mapping.",
        "How and when the artifact ZIP reached the environment and whether the extraction read exactly this archive "
        "(only content identity is established).",
    ]

    return {
        "experiment": "T11 original artifact timestamp and content provenance",
        "generated_utc": utc(time.time()),
        "mode": "SELF_TEST (harness validation only — not provenance evidence)" if selftest else "ORIGINAL ARTIFACT",
        "artifact": {"repository": REPO, "run_id": RUN_ID, "artifact_id": ARTIFACT_ID, "name": ARTIFACT_NAME,
                     "member": MEMBER, "acquisition": acquisition},
        "historical_reference": HISTORICAL,
        "classification_counts": cls_count,
        "operations": ops,
        "unrelated_entries_extracted_in": unrelated,
        "what_the_original_zip_proves": zip_proves,
        "what_the_experiments_reproduce": experiments,
        "what_historical_metadata_supports": hist_supports,
        "eliminated_mechanisms": eliminated,
        "unknown_without_platform_telemetry": unknowns,
        "filesystem": fsinfo,
        "interpretation_rules": {
            "REPRODUCED": "per scenario — the output matches every recorded historical attribute (UID, GID, mode, "
                          "size, SHA-256, parent UID/GID/mode)",
            "CONSISTENT": "per operation — at least one configuration reproduces the recorded attributes, but other "
                          "operations do too, so it cannot be uniquely identified",
            "RULED_OUT": "conflicts with an established historical attribute, or the identity cannot perform the "
                         "operation in that directory",
            "INCONCLUSIVE": "the required historical metadata (timestamps) is unavailable, or the run did not "
                            "produce a verifiable output"},
        "disclaimer": ("These experiments characterise file-handling mechanics on a GitHub-hosted runner. They do not "
                       "identify the actual ChatGPT extraction mechanism and make no claim about its internal "
                       "filesystem or processes."),
    }


def write_markdown(path: Path, rep: dict, results: dict) -> None:
    a = rep["artifact"]["acquisition"]
    L = ["# T11 — original artifact timestamp and content provenance", ""]
    if rep["mode"].startswith("SELF_TEST"):
        L += ["> **SELF_TEST MODE — harness validation with a stand-in archive. NOT provenance evidence.**", ""]
    L += [f"Generated (UTC): `{rep['generated_utc']}` · Mode: **{rep['mode']}**", "",
          f"Artifact `{rep['artifact']['name']}` (ID {rep['artifact']['artifact_id']}, run {rep['artifact']['run_id']}), "
          f"member `{rep['artifact']['member']}` — acquisition: {a.get('method')}, ok={a.get('ok')}, "
          f"SHA-256 `{a.get('sha256')}`.", "",
          f"Scenario classification counts: {rep['classification_counts']}", ""]
    for title, key in (("1. What the original ZIP proves", "what_the_original_zip_proves"),
                       ("2. What the experiments reproduce", "what_the_experiments_reproduce"),
                       ("3. What historical metadata supports", "what_historical_metadata_supports")):
        L += [f"## {title}", ""] + [f"- {x}" for x in rep[key]] + [""]
    L += ["## Timestamp and ownership behaviour per operation", "",
          "| Op | Title | Verdict | Reproduced in | mtime behaviour | example mtime (UTC) | ctime set by | "
          "time syscalls | ownership syscalls | rename: inode / mtime kept, ctime changed |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for o in rep["operations"]:
        rn = ("—" if o.get("rename_preserved_inode") is None else
              f"{o['rename_preserved_inode']} / {o['rename_preserved_mtime']}, {o['rename_changed_ctime']} "
              f"(temp {o.get('temp_owner_before_rename')})")
        L.append(f"| {o['operation']} | {o['title']} | **{o['verdict']}** | {', '.join(o['reproduced_in']) or '—'} | "
                 f"{'; '.join(o['mtime_behaviour']) or '—'} | {o.get('example_mtime_utc', '—')} | "
                 f"{o.get('ctime_set_by', '—')} | {', '.join(o['timestamp_syscalls']) or 'none'} | "
                 f"{', '.join(o['ownership_syscalls']) or 'none'} | {rn} |")
    L += ["", "## Eliminated mechanisms", ""] + [f"- {x}" for x in rep["eliminated_mechanisms"]]
    L += ["", "## 4. Unknown without platform-side telemetry", ""] + [f"- {x}" for x in rep["unknown_without_platform_telemetry"]]
    L += ["", "## Per-scenario results", "",
          "| Scenario | Class | File uid:gid mode | Parent | mtime (UTC) | ctime (UTC) | birth | inode | SHA ok | Reason |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for s in results["scenarios"]:
        ev = s["evaluation"]
        t, d = ev["target"], ev["dest_dir"]
        L.append(f"| {s['id']} | {ev['classification']} | "
                 f"{(str(t['uid']) + ':' + str(t['gid']) + ' ' + t['perm_octal']) if t else '—'} | "
                 f"{(str(d['uid']) + ':' + str(d['gid']) + ' ' + d['perm_octal']) if d else '—'} | "
                 f"{t['mtime_utc'] if t else '—'} | {t['ctime_utc'] if t else '—'} | {(t or {}).get('birth_human', '—')} | "
                 f"{t['inode'] if t else '—'} | {ev['content_sha256_ok']} | {ev['reason'][:110]} |")
    L += ["", f"> {rep['disclaimer']}", ""]
    path.write_text("\n".join(L), encoding="utf-8")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="T11 original artifact timestamp and content provenance")
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="/tmp")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--download", action="store_true", help="download the original artifact (GITHUB_TOKEN)")
    src.add_argument("--artifact-zip", help="path to an already downloaded original artifact ZIP")
    src.add_argument("--self-test", action="store_true", help="harness validation with a stand-in archive")
    ap.add_argument("--ops", nargs="*", default=None)
    ap.add_argument("--allow-root", action="store_true")
    a = ap.parse_args()

    if os.geteuid() == 0 and not a.allow_root:
        print("refusing to orchestrate as root (use --allow-root)", file=sys.stderr)
        return 2
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    base = Path(a.base).resolve()
    lab_root = Path(tempfile.mkdtemp(prefix="archbound-t11-", dir=base))
    os.chmod(lab_root, 0o711)
    canary = lab_root / "_CANARY_DO_NOT_TOUCH"
    canary.write_text("confinement canary\n")
    canary_hash = sha256_path(canary)
    origin = lab_root / "origin"
    origin.mkdir(mode=0o755)
    zip_path = origin / "artifact.zip"
    expect = dict(EXPECTED)

    # 1. Acquire
    if a.download:
        acq = download_artifact(zip_path)
    elif a.artifact_zip:
        shutil.copyfile(a.artifact_zip, zip_path)
        acq = {"method": f"pre-downloaded file {Path(a.artifact_zip).name}", "ok": True, "sha256": sha256_path(zip_path),
               "bytes": zip_path.stat().st_size}
    else:
        acq = build_selftest_zip(zip_path)
        expect.update({"zip_sha256": acq["sha256"], "png_sha256": acq["png_sha256"], "png_size": acq["png_size"]})
    os.environ.pop("GITHUB_TOKEN", None)
    acq["expected_sha256"] = expect["zip_sha256"]
    acq["sha256_verified"] = acq.get("ok") and acq.get("sha256") == expect["zip_sha256"]
    write_json(out / "artifact_acquisition.json", acq)

    def stop(reason: str, code: int = 5) -> int:
        write_json(out / "provenance_report.json", {"experiment": "T11", "status": "STOPPED", "reason": reason,
                                                    "acquisition": acq})
        (out / "provenance_report.md").write_text(
            f"# T11 — STOPPED\n\nOriginal-artifact tests were not run.\n\nReason: {reason}\n\n"
            f"Acquisition record: `artifact_acquisition.json`. No synthetic fallback was used.\n")
        print(f"T11 STOPPED: {reason}", file=sys.stderr)
        shutil.rmtree(lab_root, ignore_errors=True)
        return code

    if not acq.get("ok"):
        return stop(f"original artifact unavailable: {acq.get('error')}")
    if not acq["sha256_verified"]:
        return stop(f"archive SHA-256 {acq.get('sha256')} does not match expected {expect['zip_sha256']}")
    os.chmod(zip_path, 0o644)

    # 2. Binary/metadata inspection and member verification
    manifest = parse_zip(zip_path, MEMBER)
    manifest["archive_sha256"] = acq["sha256"]
    write_json(out / "original_zip_manifest.json", manifest)
    verification = verify_member(zip_path, MEMBER, manifest, expect)
    write_json(out / "original_png_verification.json", verification)
    if not verification["checks"]["sha256"]["pass"] or not verification["checks"]["size"]["pass"]:
        return stop("member SHA-256/size verification failed")

    search = search_historical()
    write_json(out / "historical_metadata_search.json", search)
    fsinfo = filesystem_info(lab_root)
    write_json(out / "filesystem_info.json", fsinfo)

    ut = next((x for x in manifest["extended_timestamp_fields"] if x.get("mtime_epoch") is not None), None)

    # 3. Scenarios (privileged runner)
    cmd = ["sudo", "-n", WRAPPER, str(base), "--lab-root", str(lab_root), "--zip", str(zip_path),
           "--expect-zip-sha", expect["zip_sha256"], "--expect-png-sha", expect["png_sha256"],
           "--expect-png-size", str(expect["png_size"])]
    if ut:
        cmd += ["--ut-mtime", str(ut["mtime_epoch"])]
    if a.ops:
        cmd += ["--ops", *a.ops]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3000)
    (out / "runner_stdout.txt").write_text(proc.stdout)
    (out / "runner_stderr.txt").write_text(proc.stderr)
    print(proc.stdout, end="")
    rp = lab_root / "results.json"
    if not rp.exists():
        print(proc.stderr, file=sys.stderr)
        return stop("privileged scenario runner produced no results", 4)
    results = json.loads(rp.read_text())
    results["runner_seconds"] = round(time.time() - t0, 1)

    # 4. Evidence collection
    for s in results["scenarios"]:
        d = out / "scenarios" / s["id"]
        (d / "traces").mkdir(parents=True, exist_ok=True)
        for t in sorted((lab_root / s["id"] / "traces").glob("*.strace")):
            shutil.copyfile(t, d / "traces" / t.name)
        write_json(d / "result.json", {k: v for k, v in s.items() if k not in ("snapshot_before", "steps")})
        write_json(d / "snapshot_before.json", s["snapshot_before"])
        write_json(d / "steps.json", s["steps"])
        write_json(d / "snapshot_after.json", s["steps"][-1]["snapshot_after"] if s["steps"] else {})
    write_json(out / "results.json", results)

    expected_top = {canary.name, "origin", "results.json"} | {s["id"] for s in results["scenarios"]}
    breaches = sorted(e for e in os.listdir(lab_root) if e not in expected_top)
    canary_ok = canary.exists() and sha256_path(canary) == canary_hash

    rep = build_report(results, manifest, verification, acq, fsinfo, search, a.self_test)
    rep["confinement"] = {"lab_root": str(lab_root), "canary_ok": canary_ok, "unexpected_entries": breaches}
    rep["instrumentation"] = {"strace": results["strace"], "filter": results["strace_filter"], "umask": results["umask"],
                              "default_tz": results["default_tz"], "identity_probe": results["identity_probe"],
                              "source_mtime_sentinel_utc": results["source_mtime_sentinel_utc"],
                              "runner_seconds": results["runner_seconds"]}
    write_json(out / "provenance_report.json", rep)
    write_markdown(out / "provenance_report.md", rep, results)
    readme = HERE / "T11_README.md"
    if readme.exists():
        shutil.copyfile(readme, out / "T11_README.md")

    print(f"\nmode={rep['mode']} counts={rep['classification_counts']}")
    for o in rep["operations"]:
        print(f"  {o['operation']:34s} {o['verdict']:12s} mtime={'; '.join(o['mtime_behaviour'])} "
              f"reproduced={o['reproduced_in']}")
    print(f"unrelated entries extracted in: {rep['unrelated_entries_extracted_in'] or 'none'}")
    print(f"canary_ok={canary_ok} breaches={breaches}")
    subprocess.run(["sudo", "-n", WRAPPER, str(base), "--lab-root", str(lab_root), "--cleanup"],
                   capture_output=True, text=True, timeout=300)
    return 3 if (breaches or not canary_ok) else 0


if __name__ == "__main__":
    sys.exit(main())
