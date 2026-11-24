#!/usr/bin/env python3
"""T10 privileged scenario runner — invoked only via extraction_provenance_wrapper.sh.

Runs as root. For each scenario it executes an ordered list of steps, each as
root (uid 0) or as an unprivileged identity (uid 1001 / gid 1001, dropped in the
child before exec). Every step runs under `strace -f` and is followed by an
ownership snapshot of the scenario's disposable tree, so each ownership
transition is attributable to a specific step and syscall.

The scenario tree mimics the historical layout inside a disposable lab:
    <lab>/<scenario>/mnt/data/gh_demo/screenshots/01_github.com_01.png
Nothing outside the lab is touched. Synthetic files only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

IDS = {"root": (0, 0), "unpriv": (1001, 1001)}
REL_PNG = "mnt/data/gh_demo/screenshots/01_github.com_01.png"
REL_SS = "mnt/data/gh_demo/screenshots"
REL_GH = "mnt/data/gh_demo"
REL_DATA = "mnt/data"
STRACE_FILTER = "trace=%file,%desc,%creds,umask"
KEY_SYSCALLS = ("chown", "chmod", "rename", "link", "unlink", "mkdir", "O_CREAT",
                "setuid", "setgid", "setgroups", "umask", "utime", "copy_file_range")

EXTRACT = "import zipfile,sys;zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])"
COPY2 = "import shutil,sys;shutil.copy2(sys.argv[1],sys.argv[2])"

M = "{L}/mnt/data"
GH = "{L}/mnt/data/gh_demo"
SS = GH + "/screenshots"
PNG = SS + "/01_github.com_01.png"
ST = "{L}/staging"
RS = "{L}/rootstage"
ZIP = "{O}/payload.zip"
ZIPD = "{O}/payload_direntry.zip"
SRC = "{O}/standin.png"


def R(label, *argv, umask=0o022):
    return {"who": "root", "label": label, "argv": list(argv), "umask": umask}


def U(label, *argv, umask=0o022):
    return {"who": "unpriv", "label": label, "argv": list(argv), "umask": umask}


def curl(dst, who=R, src=ZIP):
    return who(f"download ZIP -> {dst.replace('{L}/', '')}", "curl", "-sS", "-o", dst, "file://" + src)


def unzip(zip_path, dest, who=R, extra=(), umask=0o022):
    return who(f"unzip {' '.join(extra)} -> {dest.replace('{L}/', '')}".replace("  ", " "),
               "unzip", "-q", *extra, zip_path, "-d", dest, umask=umask)


def extractall(zip_path, dest, who=R, umask=0o022):
    return who(f"zipfile.extractall -> {dest.replace('{L}/', '')}", "{PY}", "-I", "-c",
               EXTRACT, zip_path, dest, umask=umask)


def mnt_plain():
    return [R("setup: mkdir -p /mnt/data (0:0 0755)", "mkdir", "-p", M),
            R("setup: chmod 0755 /mnt/data", "chmod", "0755", M)]


def mnt_setgid(mode="2755"):
    return [R("setup: mkdir -p /mnt/data", "mkdir", "-p", M),
            R("setup: chown 0:1001 /mnt/data", "chown", "0:1001", M),
            R(f"setup: chmod {mode} /mnt/data", "chmod", mode, M)]


def staging_unpriv():
    return [R("setup: mkdir staging", "mkdir", "-p", ST),
            R("setup: chown 1001:1001 staging", "chown", "1001:1001", ST)]


def rootstage():
    return [R("setup: mkdir rootstage (0:0)", "mkdir", "-p", RS)]


def unpriv_target_in_setgid():
    """Unprivileged identity extracts first, leaving a 1001:1001 target file."""
    return mnt_setgid("2775") + staging_unpriv() + [
        curl(ST + "/gh_demo.zip", who=U),
        unzip(ST + "/gh_demo.zip", GH, who=U),
    ]


def S(sid, q, title, mechanism, steps, assumptions=(), explicit_ops=False):
    return {"id": sid, "question": q, "title": title, "mechanism": mechanism,
            "assumptions": list(assumptions), "explicit_ownership_ops": explicit_ops,
            "steps": steps}


SCENARIOS = [
    # Q1 — root download followed by root extraction
    S("S1a", 1, "Root download, root unzip, new gh_demo under setgid /mnt/data (group 1001)",
      "root unzip into a new directory beneath a setgid group-1001 parent",
      mnt_setgid() + [curl(M + "/gh_demo.zip"), unzip(M + "/gh_demo.zip", GH)],
      ["/mnt/data is setgid with group 1001"]),
    S("S1b", 1, "Root download, root unzip, new gh_demo under plain /mnt/data (0:0 0755)",
      "root unzip with no setgid anywhere in the path",
      mnt_plain() + [curl(M + "/gh_demo.zip"), unzip(M + "/gh_demo.zip", GH)]),
    S("S1c", 1, "Root download, root zipfile.extractall under setgid /mnt/data",
      "root Python zipfile extraction beneath a setgid group-1001 parent",
      mnt_setgid() + [curl(M + "/gh_demo.zip"), extractall(M + "/gh_demo.zip", GH)],
      ["/mnt/data is setgid with group 1001"]),
    S("S1d", 1, "Root unzip under setgid /mnt/data with umask 002",
      "root unzip, permissive umask (mode sensitivity)",
      mnt_setgid() + [curl(M + "/gh_demo.zip"), unzip(M + "/gh_demo.zip", GH, umask=0o002)],
      ["/mnt/data is setgid with group 1001", "umask 002 at extraction"]),
    S("S1e", 1, "Root zipfile.extractall under setgid /mnt/data with umask 002",
      "root Python extraction, permissive umask (mode sensitivity)",
      mnt_setgid() + [curl(M + "/gh_demo.zip"), extractall(M + "/gh_demo.zip", GH, umask=0o002)],
      ["/mnt/data is setgid with group 1001", "umask 002 at extraction"]),

    # Q2 — root download followed by unprivileged materialization and extraction
    S("S2a", 2, "Root download, unprivileged copy (materialize) + unprivileged unzip",
      "unprivileged unzip into setgid group-1001 tree",
      mnt_setgid("2775") + staging_unpriv() + [
          curl(M + "/gh_demo.zip"),
          U("materialize: cp ZIP -> staging", "cp", M + "/gh_demo.zip", ST + "/gh_demo.zip"),
          unzip(ST + "/gh_demo.zip", GH, who=U)],
      ["/mnt/data is setgid 2775 group 1001 (group-writable)"]),
    S("S2b", 2, "Root download, unprivileged copy + unprivileged zipfile.extractall",
      "unprivileged Python extraction into setgid group-1001 tree",
      mnt_setgid("2775") + staging_unpriv() + [
          curl(M + "/gh_demo.zip"),
          U("materialize: cp ZIP -> staging", "cp", M + "/gh_demo.zip", ST + "/gh_demo.zip"),
          extractall(ST + "/gh_demo.zip", GH, who=U)],
      ["/mnt/data is setgid 2775 group 1001 (group-writable)"]),

    # Q3 — unprivileged download/materialization followed by privileged extraction
    S("S3a", 3, "Unprivileged download, root unzip under setgid /mnt/data",
      "privileged unzip of an unprivileged-owned ZIP beneath a setgid group-1001 parent",
      mnt_setgid() + staging_unpriv() + [curl(ST + "/gh_demo.zip", who=U), unzip(ST + "/gh_demo.zip", GH)],
      ["/mnt/data is setgid with group 1001"]),
    S("S3b", 3, "Unprivileged download, root unzip under plain /mnt/data",
      "privileged unzip of an unprivileged-owned ZIP, no setgid",
      mnt_plain() + staging_unpriv() + [curl(ST + "/gh_demo.zip", who=U), unzip(ST + "/gh_demo.zip", GH)]),
    S("S3c", 3, "Unprivileged download, root zipfile.extractall under setgid /mnt/data",
      "privileged Python extraction of an unprivileged-owned ZIP beneath a setgid parent",
      mnt_setgid() + staging_unpriv() + [curl(ST + "/gh_demo.zip", who=U), extractall(ST + "/gh_demo.zip", GH)],
      ["/mnt/data is setgid with group 1001"]),

    # Q4 — existing setgid destination versus newly created nested directories
    S("S4a", 4, "Pre-existing gh_demo explicitly set 0:1001 2755 under plain /mnt/data, root unzip",
      "root unzip into an explicitly configured setgid destination",
      mnt_plain() + rootstage() + [
          R("setup: mkdir gh_demo", "mkdir", GH),
          R("setup: chown 0:1001 gh_demo", "chown", "0:1001", GH),
          R("setup: chmod 2755 gh_demo", "chmod", "2755", GH),
          curl(RS + "/gh_demo.zip"), unzip(RS + "/gh_demo.zip", GH)],
      ["gh_demo explicitly chgrp 1001 + chmod 2755 before extraction"], explicit_ops=False),
    S("S4b", 4, "gh_demo created by mkdir beneath setgid /mnt/data (inherits), then root unzip",
      "inherited setgid destination created before extraction",
      mnt_setgid() + rootstage() + [
          R("mkdir gh_demo (inherits group+setgid)", "mkdir", GH),
          curl(RS + "/gh_demo.zip"), unzip(RS + "/gh_demo.zip", GH)],
      ["/mnt/data is setgid with group 1001"]),
    S("S4c", 4, "gh_demo chgrp 1001 WITHOUT setgid (0755), root unzip",
      "group-1001 destination lacking the setgid bit",
      mnt_plain() + rootstage() + [
          R("setup: mkdir gh_demo", "mkdir", GH),
          R("setup: chown 0:1001 gh_demo", "chown", "0:1001", GH),
          curl(RS + "/gh_demo.zip"), unzip(RS + "/gh_demo.zip", GH)]),
    S("S4d", 4, "Archive with explicit 'screenshots/' directory entry (040755), root unzip under setgid",
      "root unzip where the archive carries a directory entry",
      mnt_setgid() + rootstage() + [curl(RS + "/gh_demo.zip", src=ZIPD), unzip(RS + "/gh_demo.zip", GH)],
      ["/mnt/data is setgid with group 1001", "archive contains a directory entry (not stated in evidence)"]),
    S("S4e", 4, "Archive with explicit directory entry, root zipfile.extractall under setgid",
      "root Python extraction where the archive carries a directory entry",
      mnt_setgid() + rootstage() + [curl(RS + "/gh_demo.zip", src=ZIPD), extractall(RS + "/gh_demo.zip", GH)],
      ["/mnt/data is setgid with group 1001", "archive contains a directory entry (not stated in evidence)"]),

    # Q5 — direct file copying versus ZIP extraction
    S("S5a", 5, "Root cp (no -p) of an unprivileged-owned PNG into setgid screenshots/",
      "plain copy creates a new inode that inherits the directory group",
      mnt_setgid() + staging_unpriv() + [
          R("mkdir -p gh_demo/screenshots (inherit)", "mkdir", "-p", SS),
          U("stage PNG (1001:1001)", "cp", SRC, ST + "/01_github.com_01.png"),
          R("cp PNG -> screenshots/", "cp", ST + "/01_github.com_01.png", PNG)],
      ["/mnt/data is setgid with group 1001", "PNG copied rather than extracted"]),
    S("S5b", 5, "Root cp -p of an unprivileged-owned PNG",
      "ownership-preserving copy",
      mnt_setgid() + staging_unpriv() + [
          R("mkdir -p gh_demo/screenshots (inherit)", "mkdir", "-p", SS),
          U("stage PNG (1001:1001)", "cp", SRC, ST + "/01_github.com_01.png"),
          R("cp -p PNG -> screenshots/", "cp", "-p", ST + "/01_github.com_01.png", PNG)]),
    S("S5c", 5, "Root cp -p of a root-owned (0:0) PNG",
      "ownership-preserving copy of a root-owned source",
      mnt_setgid() + rootstage() + [
          R("mkdir -p gh_demo/screenshots (inherit)", "mkdir", "-p", SS),
          R("stage PNG (0:0)", "cp", SRC, RS + "/01_github.com_01.png"),
          R("cp -p PNG -> screenshots/", "cp", "-p", RS + "/01_github.com_01.png", PNG)]),
    S("S5d", 5, "Root install -m 0644",
      "install(1) creating a new file",
      mnt_setgid() + rootstage() + [
          R("mkdir -p gh_demo/screenshots (inherit)", "mkdir", "-p", SS),
          R("stage PNG (0:0)", "cp", SRC, RS + "/01_github.com_01.png"),
          R("install -m 0644 PNG", "install", "-m", "0644", RS + "/01_github.com_01.png", PNG)],
      ["/mnt/data is setgid with group 1001", "PNG installed rather than extracted"]),
    S("S5e", 5, "Root shutil.copy2",
      "metadata-copying Python copy (mode/times, not owner)",
      mnt_setgid() + rootstage() + [
          R("mkdir -p gh_demo/screenshots (inherit)", "mkdir", "-p", SS),
          R("stage PNG (0:0)", "cp", SRC, RS + "/01_github.com_01.png"),
          R("shutil.copy2 PNG", "{PY}", "-I", "-c", COPY2, RS + "/01_github.com_01.png", PNG)],
      ["/mnt/data is setgid with group 1001", "PNG copied with shutil.copy2"]),
    S("S5f", 5, "Root shell redirection (cat src > dst)",
      "new file via O_CREAT redirection",
      mnt_setgid() + rootstage() + [
          R("mkdir -p gh_demo/screenshots (inherit)", "mkdir", "-p", SS),
          R("cat SRC > PNG", "sh", "-c", f'cat "$1" > "$2"', "sh", SRC, PNG)],
      ["/mnt/data is setgid with group 1001", "PNG written by redirection"]),

    # Q6 — ownership changes via chown, rename, copy, atomic replacement
    S("S6a", 6, "Unprivileged unzip into setgid tree, then root chown 0 (file and dirs)",
      "post-hoc chown of the owner only",
      unpriv_target_in_setgid() + [
          R("chown 0 gh_demo", "chown", "0", GH),
          R("chown 0 screenshots", "chown", "0", SS),
          R("chown 0 PNG", "chown", "0", PNG)],
      ["/mnt/data is setgid 2775 group 1001", "explicit root chown after extraction"], explicit_ops=True),
    S("S6b", 6, "Unprivileged unzip in staging, root chown -R 0:1001 + chmod 2755, then mv into /mnt/data",
      "post-hoc chown/chmod then rename",
      mnt_plain() + staging_unpriv() + [
          curl(ST + "/gh_demo.zip", who=U),
          unzip(ST + "/gh_demo.zip", ST + "/gh_demo", who=U),
          R("chown -R 0:1001 staging/gh_demo", "chown", "-R", "0:1001", ST + "/gh_demo"),
          R("chmod 2755 staging/gh_demo", "chmod", "2755", ST + "/gh_demo"),
          R("mv staging/gh_demo -> /mnt/data/gh_demo", "mv", ST + "/gh_demo", GH)],
      ["explicit root chown -R 0:1001 and chmod 2755", "rename into /mnt/data"], explicit_ops=True),
    S("S6c", 6, "Root-created 0:0 PNG renamed (mv) into setgid screenshots/",
      "rename preserves the inode's original group",
      mnt_setgid() + rootstage() + [
          R("mkdir -p gh_demo/screenshots (inherit)", "mkdir", "-p", SS),
          R("stage PNG (0:0)", "cp", SRC, RS + "/01_github.com_01.png"),
          R("mv PNG -> screenshots/", "mv", RS + "/01_github.com_01.png", PNG)]),
    S("S6d", 6, "Unprivileged-created 1001:1001 PNG renamed (mv) by root into setgid screenshots/",
      "rename of an unprivileged-owned inode",
      mnt_setgid() + staging_unpriv() + [
          R("mkdir -p gh_demo/screenshots (inherit)", "mkdir", "-p", SS),
          U("stage PNG (1001:1001)", "cp", SRC, ST + "/01_github.com_01.png"),
          R("mv PNG -> screenshots/", "mv", ST + "/01_github.com_01.png", PNG)]),
    S("S6e", 6, "Atomic replacement: temp file in the same setgid dir, then rename over target",
      "same-directory temp + rename(2)",
      mnt_setgid() + rootstage() + [
          curl(RS + "/gh_demo.zip"), unzip(RS + "/gh_demo.zip", GH),
          R("write temp in screenshots/", "cp", SRC, SS + "/.01_github.com_01.png.tmp"),
          R("mv -f temp over PNG", "mv", "-f", SS + "/.01_github.com_01.png.tmp", PNG)],
      ["/mnt/data is setgid with group 1001", "atomic replace after extraction"]),
    S("S6f", 6, "Atomic replacement: temp file created elsewhere (0:0), renamed over target",
      "cross-directory temp + rename(2)",
      mnt_setgid() + rootstage() + [
          curl(RS + "/gh_demo.zip"), unzip(RS + "/gh_demo.zip", GH),
          R("write temp in rootstage (0:0)", "cp", SRC, RS + "/.png.tmp"),
          R("mv -f temp over PNG", "mv", "-f", RS + "/.png.tmp", PNG)]),
    S("S6g", 6, "Root cp overwrite of an existing unprivileged-owned PNG",
      "in-place overwrite (O_TRUNC) keeps the existing inode owner",
      unpriv_target_in_setgid() + [R("cp SRC over existing PNG", "cp", SRC, PNG)]),
    S("S6h", 6, "Root unzip -o over an existing unprivileged-owned PNG",
      "unzip overwrite of an existing file",
      unpriv_target_in_setgid() + [curl(M + "/gh_demo.zip"), unzip(M + "/gh_demo.zip", GH, extra=("-o",))]),
    S("S6i", 6, "Root zipfile.extractall over an existing unprivileged-owned PNG",
      "Python extraction overwrite of an existing file",
      unpriv_target_in_setgid() + [curl(M + "/gh_demo.zip"), extractall(M + "/gh_demo.zip", GH)]),
]


# --------------------------------------------------------------------------- #
def _fail(msg):
    print(f"extraction_provenance_helper: REFUSED: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _ts(ns):
    import datetime
    s, n = divmod(ns, 1_000_000_000)
    return datetime.datetime.fromtimestamp(s, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{n:09d}Z"


def _sha(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    h = hashlib.sha256()
    try:
        while chunk := os.read(fd, 65536):
            h.update(chunk)
    finally:
        os.close(fd)
    return h.hexdigest()


def describe(path):
    st = os.lstat(path)
    m = st.st_mode
    t = ("symlink" if stat.S_ISLNK(m) else "dir" if stat.S_ISDIR(m)
         else "regular" if stat.S_ISREG(m) else "other")
    d = {"type": t, "uid": st.st_uid, "gid": st.st_gid,
         "perm_octal": format(stat.S_IMODE(m), "04o"), "setgid": bool(m & stat.S_ISGID),
         "inode": st.st_ino, "nlink": st.st_nlink, "size": st.st_size,
         "mtime_utc": _ts(st.st_mtime_ns), "ctime_utc": _ts(st.st_ctime_ns)}
    if t == "regular":
        d["sha256"] = _sha(path)
    return d


def snapshot(lab):
    out = {}
    for dirpath, dirnames, filenames in os.walk(lab, followlinks=False):
        if "traces" in dirnames and Path(dirpath) == lab:
            dirnames.remove("traces")
        for name in dirnames + filenames:
            full = Path(dirpath) / name
            try:
                out[os.path.relpath(full, lab)] = describe(full)
            except OSError as exc:
                out[os.path.relpath(full, lab)] = {"type": "error", "error": str(exc)}
    return out


TRACKED = ("type", "uid", "gid", "perm_octal", "inode", "size", "sha256", "mtime_utc")


def transitions(prev, cur):
    out = []
    for rel in sorted(set(prev) | set(cur)):
        a, b = prev.get(rel), cur.get(rel)
        if a is None:
            out.append({"path": rel, "event": "created",
                        "state": f"{b.get('uid')}:{b.get('gid')} {b.get('perm_octal')} ino={b.get('inode')}"})
        elif b is None:
            out.append({"path": rel, "event": "removed"})
        else:
            delta = {k: [a.get(k), b.get(k)] for k in TRACKED if a.get(k) != b.get(k)}
            if delta:
                out.append({"path": rel, "event": "changed", "delta": delta})
    return out


def _preexec(uid, gid, umask):
    def fn():
        if (uid, gid) != (0, 0):
            os.setgroups([gid])
            os.setgid(gid)
            os.setuid(uid)
        os.umask(umask)
    return fn


def _resolve(argv, ctx):
    out = [a.replace("{L}", ctx["L"]).replace("{O}", ctx["O"]).replace("{PY}", ctx["PY"]) for a in argv]
    if out[0] != ctx["PY"]:
        exe = shutil.which(out[0])
        if exe:
            out[0] = exe
    return out


def run_scenario(sc, lab_root, origin, strace, unpriv_strace_ok):
    lab = lab_root / sc["id"]
    lab.mkdir(mode=0o755)
    traces = lab / "traces"
    traces.mkdir()
    os.chmod(traces, 0o1777)
    ctx = {"L": str(lab), "O": str(origin), "PY": sys.executable}
    prev = snapshot(lab)
    steps = []
    for i, step in enumerate(sc["steps"], 1):
        uid, gid = IDS[step["who"]]
        argv = _resolve(step["argv"], ctx)
        log = traces / f"{i:02d}.strace"
        traced = bool(strace) and (step["who"] == "root" or unpriv_strace_ok)
        cmd = ([strace, "-f", "-tt", "-y", "-e", STRACE_FILTER, "-o", str(log), "--"] if traced else []) + argv
        start = time.time_ns()
        try:
            with open(os.devnull, "rb") as dn:
                p = subprocess.run(cmd, stdin=dn, capture_output=True, text=True, timeout=90,
                                   preexec_fn=_preexec(uid, gid, step["umask"]))
            rc, so, se = p.returncode, p.stdout, p.stderr
        except Exception as exc:  # noqa: BLE001
            rc, so, se = None, "", f"{type(exc).__name__}: {exc}"
        end = time.time_ns()
        cur = snapshot(lab)
        key_lines = []
        if traced and log.exists():
            try:
                os.chmod(log, 0o644)
                for line in log.read_text(errors="replace").splitlines():
                    if any(k in line for k in KEY_SYSCALLS) and "/usr/" not in line and "/etc/" not in line \
                            and "/proc/" not in line and "/lib" not in line:
                        key_lines.append(line[:400])
            except OSError:
                pass
        steps.append({
            "index": i, "label": step["label"], "who": step["who"], "uid": uid, "gid": gid,
            "umask": format(step["umask"], "04o"),
            "argv": [a.replace(str(lab), "<lab>").replace(str(origin), "<origin>") for a in argv],
            "start_utc": _ts(start), "end_utc": _ts(end), "returncode": rc,
            "stdout": so[-1500:], "stderr": se[-1500:],
            "trace_file": f"traces/{log.name}" if traced and log.exists() else None,
            "trace_status": "traced" if traced else ("strace unavailable" if not strace
                                                     else "unprivileged ptrace not permitted"),
            "key_syscalls": key_lines[:120],
            "transitions": transitions(prev, cur),
        })
        prev = cur
    return lab, steps, prev


def evaluate(lab, synthetic_sha):
    def meta(rel):
        p = lab / rel
        return describe(p) if os.path.lexists(p) else None
    f, ss, gh, data = meta(REL_PNG), meta(REL_SS), meta(REL_GH), meta(REL_DATA)
    file_match = bool(f) and f["type"] == "regular" and f["uid"] == 0 and f["gid"] == 1001 \
        and f["perm_octal"] == "0644"
    parent_match = bool(gh) and gh["type"] == "dir" and gh["uid"] == 0 and gh["gid"] == 1001 \
        and gh["perm_octal"] == "2755"
    return {"png": f, "screenshots_dir": ss, "gh_demo_dir": gh, "mnt_data_dir": data,
            "file_match": file_match, "parent_match": parent_match,
            "full_match": file_match and parent_match,
            "content_matches_synthetic": bool(f) and f.get("sha256") == synthetic_sha}


def _validate(lab_root):
    base = Path(os.environ.get("ARCHIVE_BOUNDARY_BASE") or "/tmp").resolve()
    real = Path(os.path.realpath(lab_root))
    if not str(real).startswith(str(base) + os.sep):
        _fail(f"{real} not under base {base}")
    if not real.name.startswith("archbound-t10-") or os.path.islink(lab_root):
        _fail(f"{real} is not an archbound-t10 lab")
    return real


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab-root", required=True)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--cleanup", action="store_true")
    args = ap.parse_args()
    lab_root = _validate(args.lab_root)

    if args.cleanup:
        shutil.rmtree(lab_root, ignore_errors=True)
        print(f"extraction_provenance_helper: removed {lab_root}")
        return 0

    origin = lab_root / "origin"
    if not origin.is_dir():
        _fail("origin directory missing")
    synthetic_sha = _sha(origin / "standin.png")

    strace = shutil.which("strace")
    unpriv_ok = False
    if strace:
        probe = subprocess.run([strace, "-f", "-o", "/dev/null", "--", "/bin/true"],
                               capture_output=True, preexec_fn=_preexec(1001, 1001, 0o022))
        unpriv_ok = probe.returncode == 0

    results = []
    for sc in SCENARIOS:
        if args.only and sc["id"] not in args.only:
            continue
        lab, steps, final_snapshot = run_scenario(sc, lab_root, origin, strace, unpriv_ok)
        results.append({k: v for k, v in sc.items() if k != "steps"} |
                       {"steps": steps, "final_snapshot": final_snapshot,
                        "evaluation": evaluate(lab, synthetic_sha)})
        print(f"[{sc['id']}] full_match={results[-1]['evaluation']['full_match']}")

    meta = {"helper_euid": os.geteuid(), "strace": strace, "strace_filter": STRACE_FILTER,
            "unprivileged_strace": unpriv_ok, "unprivileged_identity": IDS["unpriv"],
            "synthetic_png_sha256": synthetic_sha, "scenarios": results}
    out = lab_root / "results.json"
    out.write_text(json.dumps(meta, indent=2) + "\n")
    os.chmod(out, 0o644)
    for p in lab_root.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink() and p.suffix == ".strace":
                os.chmod(p, 0o644)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
