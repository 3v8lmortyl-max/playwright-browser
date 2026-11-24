#!/usr/bin/env python3
"""T11 privileged scenario runner — invoked only via t11_privileged_wrapper.sh (sudo).

Runs as root. For every (operation x directory profile x identity) scenario it
creates a fresh disposable directory under the lab root, performs setup as root,
then performs the operation as root (uid 0) or as an unprivileged identity
(uid 1000 / gid 1000, dropped in the child before exec). Every step runs under
`strace -f` and is followed by a full metadata snapshot (uid, gid, mode, device,
inode, nlink, size, SHA-256, atime/mtime/ctime in ns, birth time via statx).

Only the single verified archive member `screenshots/01_github.com_01.png` is
ever extracted or read. Extracted content is never executed. Snapshot reads use
O_NOATIME so that hashing does not perturb the timestamps under test.
"""

from __future__ import annotations

import argparse
import calendar
import datetime
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

MEMBER = "screenshots/01_github.com_01.png"
NAME = "01_github.com_01.png"
PROFILES = {
    "A": {"owner": 0, "group": 1001, "mode": "2755"},
    "B": {"owner": 1000, "group": 1001, "mode": "2755"},
    "C": {"owner": 1000, "group": 1000, "mode": "0755"},
}
IDENT = {"root": (0, 0), "unpriv": (1000, 1000)}
UMASK = 0o022
DEFAULT_TZ = "UTC"
ALT_TZ = "Asia/Kolkata"
SENTINEL_NS = calendar.timegm((2001, 2, 3, 4, 5, 6)) * 1_000_000_000 + 123_456_789
STRACE_FILTER = ("trace=%file,%creds,fchown,fchmod,utimensat,futimesat,copy_file_range,"
                 "sendfile,ioctl,umask,write,close,fsync,fdatasync")
KEY_TOKENS = ("utimensat", "futimesat", "utime", "chown", "chmod", "rename", "link", "unlink",
              "copy_file_range", "sendfile", "ioctl", "mkdir", "O_CREAT", "umask")
NOISE = ("/usr/", "/lib", "/etc/", "/proc/", "/sys/", "/dev/", "pycache", "/bin/")

PY_EXTRACT = "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extract(sys.argv[2],sys.argv[3])"
PY_EXTRACTALL = ("import zipfile,sys;"
                 "zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[3],members=[sys.argv[2]])")
PY_COPYFILE = "import shutil,sys;shutil.copyfile(sys.argv[1],sys.argv[2])"
PY_COPY2 = "import shutil,sys;shutil.copy2(sys.argv[1],sys.argv[2])"
PY_STREAM = ("import zipfile,shutil,sys\n"
             "with zipfile.ZipFile(sys.argv[1]) as z, z.open(sys.argv[2]) as s, "
             "open(sys.argv[3], sys.argv[4]) as d:\n    shutil.copyfileobj(s, d)\n")
PY_REPLACE = "import os,sys;os.replace(sys.argv[1],sys.argv[2])"
PY_PREP_SRC = ("import zipfile,shutil,sys,os\n"
               "with zipfile.ZipFile(sys.argv[1]) as z, z.open(sys.argv[2]) as s, "
               "open(sys.argv[3],'wb') as d:\n    shutil.copyfileobj(s, d)\n"
               "os.chown(sys.argv[3], int(sys.argv[4]), int(sys.argv[5]))\n"
               "os.chmod(sys.argv[3], 0o644)\n"
               "ns = int(sys.argv[6]); os.utime(sys.argv[3], ns=(ns, ns))\n")
PY_PREEXIST = ("import os,sys\n"
               "open(sys.argv[1],'wb').write(b'pre-existing placeholder owned by uid 1000\\n')\n"
               "os.chown(sys.argv[1], 1000, -1)\nos.chmod(sys.argv[1], 0o644)\n")

D = "{S}/dest"
SS = D + "/screenshots"
T = SS + "/" + NAME
SRC = "{S}/src/" + NAME
EW = "{S}/elsewhere"
TMP_SAME = SS + "/." + NAME + ".tmp"
TMP_OTHER = EW + "/." + NAME + ".tmp"


def X(label, *argv, env=None):
    return {"role": "op", "who": "X", "label": label, "argv": list(argv), "env": env or {}}


def ROOT(label, *argv, role="setup"):
    return {"role": role, "who": "root", "label": label, "argv": list(argv), "env": {}}


MK = X("mkdir -p dest/screenshots", "mkdir", "-p", SS)
PREEXIST = ROOT("pre-create destination file owned by uid 1000", "{PY}", "-I", "-c", PY_PREEXIST, T)


def stream(dst, mode="wb"):
    return X(f"stream ZIP member -> {dst.replace('{S}/', '')}", "{PY}", "-I", "-c", PY_STREAM,
             "{Z}", MEMBER, dst, mode)


def replace(src):
    return X(f"os.replace {src.replace('{S}/', '')} -> dest/screenshots/{NAME}", "{PY}", "-I", "-c",
             PY_REPLACE, src, T)


OPS = {
    "01_unzip": {"group": "A. Archive extraction", "title": "Info-ZIP unzip (single member)",
                 "steps": [X("unzip -q ZIP member -d dest", "unzip", "-q", "{Z}", MEMBER, "-d", D)]},
    "01tz_unzip_alt_tz": {"group": "A. Archive extraction",
                          "title": f"Info-ZIP unzip with TZ={ALT_TZ} (DOS-time zone sensitivity)",
                          "steps": [X("unzip -q ZIP member -d dest", "unzip", "-q", "{Z}", MEMBER, "-d", D,
                                      env={"TZ": ALT_TZ})]},
    "02_zipfile_extract": {"group": "A. Archive extraction", "title": "Python zipfile.extract(member)",
                           "steps": [X("zipfile.extract", "{PY}", "-I", "-c", PY_EXTRACT, "{Z}", MEMBER, D)]},
    "03_zipfile_extractall": {"group": "A. Archive extraction",
                              "title": "Python zipfile.extractall(members=[member])",
                              "steps": [X("zipfile.extractall", "{PY}", "-I", "-c", PY_EXTRACTALL, "{Z}", MEMBER, D)]},
    "04_cp": {"group": "B. Copy", "title": "cp (default)", "src": (1001, 1001),
              "steps": [MK, X("cp src dest", "cp", SRC, T)]},
    "05_cp_p": {"group": "B. Copy", "title": "cp -p (source 1001:1001)", "src": (1001, 1001),
                "steps": [MK, X("cp -p src dest", "cp", "-p", SRC, T)]},
    "05g_cp_p_src_0_1001": {"group": "B. Copy", "title": "cp -p (source 0:1001)", "src": (0, 1001),
                            "steps": [MK, X("cp -p src dest", "cp", "-p", SRC, T)]},
    "06_copyfile": {"group": "B. Copy", "title": "shutil.copyfile", "src": (1001, 1001),
                    "steps": [MK, X("shutil.copyfile", "{PY}", "-I", "-c", PY_COPYFILE, SRC, T)]},
    "07_copy2": {"group": "B. Copy", "title": "shutil.copy2", "src": (1001, 1001),
                 "steps": [MK, X("shutil.copy2", "{PY}", "-I", "-c", PY_COPY2, SRC, T)]},
    "08_stream": {"group": "B. Copy", "title": "direct stream from ZIP member to destination",
                  "steps": [MK, stream(T)]},
    "09_atomic_samedir": {"group": "C. Atomic replacement",
                          "title": "temp in destination dir, then os.replace",
                          "steps": [MK, stream(TMP_SAME, "xb"), replace(TMP_SAME)]},
    "10_atomic_otherdir": {"group": "C. Atomic replacement",
                           "title": "temp in another (plain) dir, then rename into destination",
                           "elsewhere": "plain", "steps": [MK, stream(TMP_OTHER, "xb"), replace(TMP_OTHER)]},
    "10g_atomic_otherdir_setgid1001": {"group": "C. Atomic replacement",
                                       "title": "temp in another setgid group-1001 dir, then rename",
                                       "elsewhere": "setgid1001",
                                       "steps": [MK, stream(TMP_OTHER, "xb"), replace(TMP_OTHER)]},
    "11a_atomic_samedir_over_uid1000": {"group": "C. Atomic replacement",
                                        "title": "same-dir temp + os.replace over existing uid-1000 file",
                                        "steps": [MK, PREEXIST, stream(TMP_SAME, "xb"), replace(TMP_SAME)]},
    "11b_atomic_otherdir_over_uid1000": {"group": "C. Atomic replacement",
                                         "title": "other-dir temp + rename over existing uid-1000 file",
                                         "elsewhere": "plain",
                                         "steps": [MK, PREEXIST, stream(TMP_OTHER, "xb"), replace(TMP_OTHER)]},
}


# --------------------------------------------------------------------------- #
def _fail(msg):
    print(f"t11_privileged_helper: REFUSED: {msg}", file=sys.stderr)
    raise SystemExit(2)


def ts(ns):
    s, n = divmod(int(ns), 1_000_000_000)
    return datetime.datetime.fromtimestamp(s, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") \
        + f".{n:09d}Z"


def sha_file(path):
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NOATIME", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
    h = hashlib.sha256()
    try:
        while chunk := os.read(fd, 1 << 16):
            h.update(chunk)
    finally:
        os.close(fd)
    return h.hexdigest()


def birth_times(paths):
    out = {}
    if not paths:
        return out
    try:
        p = subprocess.run(["stat", "--printf", "%n\t%W\t%w\n", "--", *paths],
                           capture_output=True, text=True, timeout=30)
        for line in p.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                out[parts[0]] = {"birth_epoch_s": int(parts[1]) if parts[1].lstrip("-").isdigit() else None,
                                 "birth_human": parts[2]}
    except Exception:  # noqa: BLE001
        pass
    return out


def describe(path, birth=None):
    st = os.lstat(path)
    m = st.st_mode
    t = ("symlink" if stat.S_ISLNK(m) else "dir" if stat.S_ISDIR(m)
         else "regular" if stat.S_ISREG(m) else "other")
    d = {"type": t, "uid": st.st_uid, "gid": st.st_gid, "perm_octal": format(stat.S_IMODE(m), "04o"),
         "setgid": bool(m & stat.S_ISGID), "dev": f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}",
         "st_dev": st.st_dev, "inode": st.st_ino, "nlink": st.st_nlink, "size": st.st_size,
         "atime_ns": st.st_atime_ns, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns,
         "atime_utc": ts(st.st_atime_ns), "mtime_utc": ts(st.st_mtime_ns), "ctime_utc": ts(st.st_ctime_ns)}
    b = (birth or {}).get(str(path))
    if b:
        d["birth_epoch_s"] = b["birth_epoch_s"]
        d["birth_human"] = b["birth_human"]
    if t == "regular":
        d["sha256"] = sha_file(path)
    return d


def snapshot(sdir):
    paths = []
    for dirpath, dirnames, filenames in os.walk(sdir, followlinks=False):
        if Path(dirpath) == sdir and "traces" in dirnames:
            dirnames.remove("traces")
        for n in dirnames + filenames:
            paths.append(str(Path(dirpath) / n))
    births = birth_times(paths)
    out = {}
    for p in paths:
        try:
            out[os.path.relpath(p, sdir)] = describe(p, births)
        except OSError as exc:
            out[os.path.relpath(p, sdir)] = {"type": "error", "error": str(exc)}
    return out


TRACKED = ("type", "uid", "gid", "perm_octal", "inode", "size", "sha256", "mtime_ns", "ctime_ns")


def transitions(prev, cur):
    out = []
    for rel in sorted(set(prev) | set(cur)):
        a, b = prev.get(rel), cur.get(rel)
        if a is None:
            out.append({"path": rel, "event": "created",
                        "state": {k: b.get(k) for k in ("uid", "gid", "perm_octal", "inode",
                                                        "size", "mtime_utc", "ctime_utc")}})
        elif b is None:
            out.append({"path": rel, "event": "removed", "last_inode": a.get("inode")})
        else:
            delta = {k: [a.get(k), b.get(k)] for k in TRACKED if a.get(k) != b.get(k)}
            if delta:
                out.append({"path": rel, "event": "changed", "delta": delta})
    return out


def _preexec(uid, gid):
    def fn():
        if (uid, gid) != (0, 0):
            os.setgroups([gid])
            os.setgid(gid)
            os.setuid(uid)
        os.umask(UMASK)
    return fn


def _resolve(argv, ctx):
    out = [a.replace("{S}", ctx["S"]).replace("{Z}", ctx["Z"]).replace("{PY}", ctx["PY"]) for a in argv]
    if out[0] != ctx["PY"]:
        exe = shutil.which(out[0])
        if exe:
            out[0] = exe
    return out


def setup_steps(op, prof, ident):
    p = PROFILES[prof]
    xuid, xgid = IDENT[ident]
    steps = [ROOT("setup: mkdir dest", "mkdir", D),
             ROOT(f"setup: chown {p['owner']}:{p['group']} dest", "chown", f"{p['owner']}:{p['group']}", D),
             ROOT(f"setup: chmod {p['mode']} dest", "chmod", p["mode"], D)]
    if "src" in op:
        su, sg = op["src"]
        steps += [ROOT("setup: mkdir src", "mkdir", "{S}/src"),
                  ROOT(f"setup: write source PNG from ZIP member, chown {su}:{sg}, mtime sentinel",
                       "{PY}", "-I", "-c", PY_PREP_SRC, "{Z}", MEMBER, SRC, str(su), str(sg), str(SENTINEL_NS))]
    if op.get("elsewhere") == "plain":
        steps += [ROOT("setup: mkdir elsewhere", "mkdir", EW),
                  ROOT(f"setup: chown {xuid}:{xgid} elsewhere", "chown", f"{xuid}:{xgid}", EW)]
    elif op.get("elsewhere") == "setgid1001":
        steps += [ROOT("setup: mkdir elsewhere", "mkdir", EW),
                  ROOT(f"setup: chown {xuid}:1001 elsewhere", "chown", f"{xuid}:1001", EW),
                  ROOT("setup: chmod 2755 elsewhere", "chmod", "2755", EW)]
    return steps


def run_scenario(sid, op, prof, ident, lab_root, zip_path, strace):
    sdir = lab_root / sid
    sdir.mkdir(mode=0o755)
    traces = sdir / "traces"
    traces.mkdir()
    os.chmod(traces, 0o1777)
    ctx = {"S": str(sdir), "Z": str(zip_path), "PY": sys.executable}
    all_steps = setup_steps(op, prof, ident) + op["steps"]
    before = snapshot(sdir)
    prev = before
    records = []
    for i, step in enumerate(all_steps, 1):
        uid, gid = IDENT[ident] if step["who"] == "X" else (0, 0)
        argv = _resolve(step["argv"], ctx)
        tz = step["env"].get("TZ", DEFAULT_TZ)
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
               "LC_ALL": "C", "TZ": tz}
        log = traces / f"{i:02d}.strace"
        cmd = ([strace, "-f", "-tt", "-y", "-s", "64", "-e", STRACE_FILTER, "-o", str(log), "--"]
               if strace else []) + argv
        start = time.time_ns()
        try:
            with open(os.devnull, "rb") as dn:
                pr = subprocess.run(cmd, stdin=dn, capture_output=True, text=True, timeout=120, env=env,
                                    preexec_fn=_preexec(uid, gid))
            rc, so, se = pr.returncode, pr.stdout, pr.stderr
        except Exception as exc:  # noqa: BLE001
            rc, so, se = None, "", f"{type(exc).__name__}: {exc}"
        end = time.time_ns()
        cur = snapshot(sdir)
        key = []
        if strace and log.exists():
            try:
                os.chmod(log, 0o644)
                for line in log.read_text(errors="replace").splitlines():
                    if any(k in line for k in KEY_TOKENS) and not any(n in line for n in NOISE):
                        key.append(line[:500])
            except OSError:
                pass
        records.append({
            "index": i, "role": step["role"], "label": step["label"], "who": step["who"],
            "euid": uid, "egid": gid, "umask": format(UMASK, "04o"), "tz": tz,
            "argv": [a.replace(str(sdir), "<scenario>").replace(str(zip_path), "<artifact.zip>")
                     .replace(sys.executable, "python3") for a in argv],
            "start_ns": start, "end_ns": end, "start_utc": ts(start), "end_utc": ts(end),
            "returncode": rc, "stdout": so[-1500:], "stderr": se[-1500:],
            "trace_file": f"traces/{log.name}" if strace and log.exists() else None,
            "key_syscalls": key[:150],
            "snapshot_after": cur,
            "transitions": transitions(prev, cur),
        })
        prev = cur
    return sdir, before, records


def dos_epoch_ns(dt_tuple, tz):
    naive = datetime.datetime(*dt_tuple)
    if ZoneInfo is None:
        return None
    return int(naive.replace(tzinfo=ZoneInfo(tz)).timestamp()) * 1_000_000_000


def evaluate(sdir, records, ctx):
    t = sdir / "dest" / "screenshots" / NAME
    paths = [p for p in (t, sdir / "dest", sdir / "dest" / "screenshots") if os.path.lexists(p)]
    births = birth_times([str(p) for p in paths])
    tm = describe(t, births) if os.path.lexists(t) else None
    dm = describe(sdir / "dest", births) if (sdir / "dest").exists() else None
    ssm = describe(sdir / "dest" / "screenshots", births) if (sdir / "dest" / "screenshots").exists() else None
    op_steps = [r for r in records if r["role"] == "op"]
    failed = [r for r in op_steps if r["returncode"] != 0]
    content_ok = bool(tm) and tm["type"] == "regular" and tm.get("sha256") == ctx["png_sha"] \
        and tm["size"] == ctx["png_size"]

    conflicts = []
    if tm and tm["type"] == "regular":
        if tm["uid"] != 0:
            conflicts.append(f"file UID {tm['uid']} (historical 0)")
        if tm["gid"] != 1001:
            conflicts.append(f"file GID {tm['gid']} (historical 1001)")
        if tm["perm_octal"] != "0644":
            conflicts.append(f"file mode {tm['perm_octal']} (historical 0644)")
    if dm:
        if dm["uid"] != 0 or dm["gid"] != 1001:
            conflicts.append(f"parent {dm['uid']}:{dm['gid']} (historical 0:1001)")
        if dm["perm_octal"] != "2755":
            conflicts.append(f"parent mode {dm['perm_octal']} (historical 2755)")

    # Timestamp attribution (exact-equality checks against known candidate sources).
    mt = {}
    if tm:
        tzs = sorted({r["tz"] for r in op_steps}) or [DEFAULT_TZ]
        cands = {f"archive DOS time interpreted in TZ={z}": dos_epoch_ns(ctx["dos_time"], z) for z in tzs}
        if ctx.get("ut_mtime") is not None:
            cands["archive extended-timestamp (UT) mtime"] = int(ctx["ut_mtime"]) * 1_000_000_000
        cands["source file mtime (sentinel, preserved)"] = SENTINEL_NS
        src = next((n for n, v in cands.items() if v is not None and v == tm["mtime_ns"]), None)
        if src is None:
            for r in records:
                if r["start_ns"] <= tm["mtime_ns"] <= r["end_ns"] + 2_000_000:
                    src = f"time of step {r['index']} ({r['label']}) — newly assigned"
                    break
        mt["mtime_source"] = src or "unattributed"
        mt["mtime_subsecond_ns"] = tm["mtime_ns"] % 1_000_000_000
        cst = next((r for r in records if r["start_ns"] <= tm["ctime_ns"] <= r["end_ns"] + 2_000_000), None)
        mt["ctime_set_by_step"] = f"{cst['index']} ({cst['label']})" if cst else "unattributed"
        mt["ctime_after_mtime"] = tm["ctime_ns"] > tm["mtime_ns"]
        # inode continuity across rename: compare to the temp file seen before the last op step
        temp_rel = [k for r in records for k in r["snapshot_after"] if k.endswith(".tmp")]
        if temp_rel and len(op_steps) >= 2:
            pre = op_steps[-2]["snapshot_after"]
            tmp_meta = next((pre[k] for k in pre if k.endswith(".tmp")), None)
            if tmp_meta:
                mt["rename_preserved_inode"] = tmp_meta["inode"] == tm["inode"]
                mt["rename_preserved_mtime"] = tmp_meta["mtime_ns"] == tm["mtime_ns"]
                mt["rename_changed_ctime"] = tmp_meta["ctime_ns"] != tm["ctime_ns"]
                mt["temp_owner_before_rename"] = f"{tmp_meta['uid']}:{tmp_meta['gid']}"
        pre_existing = [r for r in records if r["label"].startswith("pre-create")]
        if pre_existing:
            m0 = pre_existing[0]["snapshot_after"].get(f"dest/screenshots/{NAME}")
            if m0:
                mt["pre_existing_inode"] = m0["inode"]
                mt["pre_existing_owner"] = f"{m0['uid']}:{m0['gid']}"
                mt["replaced_inode"] = m0["inode"] != tm["inode"]

    ownership_syscalls = [l for r in op_steps for l in r["key_syscalls"] if "chown" in l]
    time_syscalls = [l for r in op_steps for l in r["key_syscalls"] if "utimensat" in l or "futimesat" in l]

    if failed:
        err = " ".join(r["stderr"] for r in failed)
        perm = any(s in err for s in ("Permission denied", "EACCES", "Operation not permitted", "EPERM"))
        cls = "RULED_OUT" if perm else "INCONCLUSIVE"
        reason = ("operation not permitted for this identity in this directory profile" if perm
                  else "operation failed for a reason other than permissions: " + err.strip()[-300:])
    elif not content_ok:
        cls, reason = "INCONCLUSIVE", "output content does not match the verified original PNG"
    elif conflicts:
        cls, reason = "RULED_OUT", "; ".join(conflicts)
    else:
        cls, reason = "REPRODUCED", "matches all established attributes (UID, GID, mode, size, SHA-256, parent)"

    return {"classification": cls, "reason": reason, "timestamp_classification": "INCONCLUSIVE",
            "timestamp_reason": "historical mtime/ctime have not been established",
            "content_sha256_ok": content_ok, "target": tm, "screenshots_dir": ssm, "dest_dir": dm,
            "conflicts": conflicts, "timestamps": mt,
            "ownership_syscalls_in_operation": ownership_syscalls[:20],
            "timestamp_syscalls_in_operation": time_syscalls[:20]}


def _validate(lab_root):
    base = Path(os.environ.get("ARCHIVE_BOUNDARY_BASE") or "/tmp").resolve()
    real = Path(os.path.realpath(lab_root))
    if not str(real).startswith(str(base) + os.sep) or not real.name.startswith("archbound-t11-"):
        _fail(f"{real} is not an archbound-t11 lab under {base}")
    if os.path.islink(lab_root):
        _fail("lab root is a symlink")
    return real


def identity_probe(uid, gid):
    snippet = ("import os,json;print(json.dumps({'euid':os.geteuid(),'egid':os.getegid(),"
               "'groups':sorted(os.getgroups()),'umask':format(os.umask(0o022),'04o')}))")
    try:
        p = subprocess.run([sys.executable, "-I", "-c", snippet], capture_output=True, text=True,
                           timeout=20, preexec_fn=_preexec(uid, gid))
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab-root", required=True)
    ap.add_argument("--zip")
    ap.add_argument("--expect-zip-sha")
    ap.add_argument("--expect-png-sha")
    ap.add_argument("--expect-png-size", type=int)
    ap.add_argument("--ut-mtime", type=int, default=None)
    ap.add_argument("--ops", nargs="*", default=None)
    ap.add_argument("--profiles", nargs="*", default=list(PROFILES))
    ap.add_argument("--identities", nargs="*", default=list(IDENT))
    ap.add_argument("--cleanup", action="store_true")
    a = ap.parse_args()
    lab_root = _validate(a.lab_root)
    if a.cleanup:
        shutil.rmtree(lab_root, ignore_errors=True)
        return 0

    zip_path = Path(os.path.realpath(a.zip))
    if not str(zip_path).startswith(str(lab_root) + os.sep) or not zip_path.is_file():
        _fail("archive must be a regular file inside the lab root")
    h = hashlib.sha256()
    with open(zip_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != a.expect_zip_sha:
        _fail(f"archive SHA-256 {h.hexdigest()} != expected {a.expect_zip_sha}")
    with zipfile.ZipFile(zip_path) as zf:
        info = zf.getinfo(MEMBER)
        dos_time = info.date_time

    strace = shutil.which("strace")
    ctx = {"png_sha": a.expect_png_sha, "png_size": a.expect_png_size, "dos_time": dos_time,
           "ut_mtime": a.ut_mtime}
    results = []
    for op_id, op in OPS.items():
        if a.ops and op_id not in a.ops:
            continue
        for prof in a.profiles:
            for ident in a.identities:
                sid = f"{op_id}__{prof}__{ident}"
                sdir, before, records = run_scenario(sid, op, prof, ident, lab_root, zip_path, strace)
                ev = evaluate(sdir, records, ctx)
                results.append({"id": sid, "operation": op_id, "group": op["group"], "title": op["title"],
                                "profile": prof, "profile_spec": PROFILES[prof], "identity": ident,
                                "identity_ids": IDENT[ident], "snapshot_before": before,
                                "steps": records, "evaluation": ev})
                print(f"[{sid}] {ev['classification']}")

    meta = {"helper_euid": os.geteuid(), "strace": strace, "strace_filter": STRACE_FILTER,
            "umask": format(UMASK, "04o"), "default_tz": DEFAULT_TZ, "alt_tz": ALT_TZ,
            "source_mtime_sentinel_ns": SENTINEL_NS, "source_mtime_sentinel_utc": ts(SENTINEL_NS),
            "archive_sha256_verified": h.hexdigest(), "member": MEMBER, "member_dos_time": list(dos_time),
            "identity_probe": {k: identity_probe(*v) for k, v in IDENT.items()},
            "profiles": PROFILES, "scenarios": results}
    out = lab_root / "results.json"
    out.write_text(json.dumps(meta, indent=1) + "\n")
    os.chmod(out, 0o644)
    return 0


if __name__ == "__main__":
    sys.exit(main())
