#!/usr/bin/env python3
"""Privileged extraction helper — the ONLY command invoked via sudo.

Runs as root (via a narrowly scoped sudo invocation) and does exactly one thing:
extract one archive into one lab's ``extract/`` directory using one allowlisted
extractor, then record evidence. It performs no other privileged action, runs no
shell, and refuses any destination that is not a per-test lab under the
temp base.

Scope enforcement (so a bug in the unprivileged orchestrator cannot aim root at
an arbitrary path):
  * ``--extractor`` must be in the allowlist.
  * ``--zip`` must be an existing *regular* file (not a symlink).
  * ``--lab`` must resolve under the allowed base ($ARCHIVE_BOUNDARY_BASE, else
    $RUNNER_TEMP, else /tmp) and its name must carry the 'archbound-' marker.
  * The destination is always ``<lab>/extract`` and no component of
    ``lab``/``extract`` may itself be a symlink.

The helper deliberately does NOT sanitize archive contents — sanitizing would
defeat the measurement. Confinement rests on the generator's safety gate (every
payload resolves inside its lab) plus the orchestrator's post-run guard.

Usage:
    sudo python3 privileged_extract.py --extractor unzip \
         --lab /tmp/archbound-XXXX/T1__unzip --zip <lab>/input/payload.zip
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import grp
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ALLOWED_EXTRACTORS = (
    "unzip",
    "python-zipfile-extractall",
    "python-zipfile-naive-control",
)


def _fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"privileged_extract: REFUSED: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _allowed_base() -> Path:
    base = os.environ.get("ARCHIVE_BOUNDARY_BASE") or os.environ.get("RUNNER_TEMP") or "/tmp"
    return Path(base).resolve()


def _has_symlink_component(path: Path, stop: Path) -> bool:
    """True if any component of ``path`` from ``stop`` downward is a symlink."""
    path = Path(os.path.normpath(str(path)))
    rel = path.relative_to(stop)
    cur = stop
    for part in rel.parts:
        cur = cur / part
        if os.path.islink(cur):
            return True
    return False


def _validate(lab: Path, zip_path: Path) -> Path:
    base = _allowed_base()
    lab_real = Path(os.path.realpath(lab))
    if not (str(lab_real) == str(base) or str(lab_real).startswith(str(base) + os.sep)):
        _fail(f"lab {lab_real} is not under allowed base {base}")
    if "archbound-" not in str(lab_real):
        _fail(f"lab {lab_real} lacks the 'archbound-' marker")
    if not lab_real.is_dir():
        _fail(f"lab {lab_real} is not a directory")

    dest = lab_real / "extract"
    if not dest.is_dir():
        _fail(f"extraction root {dest} is missing")
    if _has_symlink_component(dest, base):
        _fail(f"a path component of {dest} is a symlink")

    if not zip_path.exists():
        _fail(f"archive {zip_path} does not exist")
    st = os.lstat(zip_path)
    if not stat.S_ISREG(st.st_mode):
        _fail(f"archive {zip_path} is not a regular file")
    return dest


def _identity() -> dict:
    euid, egid = os.geteuid(), os.getegid()
    try:
        uname = pwd.getpwuid(euid).pw_name
    except KeyError:
        uname = str(euid)
    try:
        gname = grp.getgrgid(egid).gr_name
    except KeyError:
        gname = str(egid)
    return {
        "euid": euid,
        "egid": egid,
        "ruid": os.getuid(),
        "rgid": os.getgid(),
        "user": uname,
        "group": gname,
        "is_root": euid == 0,
        "groups": sorted(os.getgroups()),
    }


def _extractor_version(extractor: str) -> str:
    if extractor == "unzip":
        try:
            out = subprocess.run(
                ["unzip", "-v"], capture_output=True, text=True, timeout=20
            ).stdout.splitlines()
            return out[0].strip() if out else "unzip (version unknown)"
        except Exception as exc:  # noqa: BLE001
            return f"unzip (version query failed: {exc})"
    return f"python {sys.version.split()[0]} zipfile"


def _run_unzip(zip_path: Path, dest: Path) -> dict:
    # No -o: observe natural overwrite behavior (for T5). stdin from /dev/null so
    # any interactive overwrite prompt gets EOF instead of hanging.
    with open(os.devnull, "rb") as devnull:
        proc = subprocess.run(
            ["unzip", "-q", str(zip_path), "-d", str(dest)],
            stdin=devnull,
            capture_output=True,
            text=True,
            timeout=60,
        )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "error": None,
    }


def _run_extractall(zip_path: Path, dest: Path) -> dict:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest)
        return {"returncode": 0, "stdout": "", "stderr": "", "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"returncode": 1, "stdout": "", "stderr": "", "error": f"{type(exc).__name__}: {exc}"}


def _run_naive_control(zip_path: Path, dest: Path) -> dict:
    """INTENTIONALLY UNSAFE positive control.

    Reproduces the classic manual-join anti-pattern (no path sanitization,
    creates symlinks, writes through them). It exists to prove the harness can
    actually *detect* an escape; it is not a system extractor and must never be
    copied into real code. Confinement still holds because every payload target
    resolves inside the lab (generator safety gate).
    """
    errors = []
    dest = Path(dest)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0xFFFF
                target = os.path.join(str(dest), info.filename)  # UNSAFE on purpose
                parent = os.path.dirname(target)
                try:
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    if stat.S_ISLNK(mode):
                        link_target = zf.read(info).decode("utf-8", "replace")
                        if os.path.lexists(target):
                            os.remove(target)
                        os.symlink(link_target, target)
                    elif info.filename.endswith("/"):
                        os.makedirs(target, exist_ok=True)
                    else:
                        with open(target, "wb") as fh:  # follows a pre-existing symlink
                            fh.write(zf.read(info))
                        if mode & 0o7777:
                            os.chmod(target, mode & 0o7777)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{info.filename}: {type(exc).__name__}: {exc}")
        return {
            "returncode": 1 if errors else 0,
            "stdout": "",
            "stderr": "\n".join(errors),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"returncode": 1, "stdout": "", "stderr": "", "error": f"{type(exc).__name__}: {exc}"}


EXTRACTORS = {
    "unzip": _run_unzip,
    "python-zipfile-extractall": _run_extractall,
    "python-zipfile-naive-control": _run_naive_control,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Privileged archive-extraction helper.")
    ap.add_argument("--extractor", required=True, choices=ALLOWED_EXTRACTORS)
    ap.add_argument("--lab", required=True, help="per-test lab directory")
    ap.add_argument("--zip", required=True, dest="zip_path", help="archive to extract")
    args = ap.parse_args()

    lab = Path(args.lab)
    zip_path = Path(args.zip_path)
    dest = _validate(lab, zip_path)

    identity = _identity()
    result = EXTRACTORS[args.extractor](zip_path, dest)

    meta = {
        "extractor": args.extractor,
        "extractor_version": _extractor_version(args.extractor),
        "identity": identity,
        "zip": str(zip_path),
        "dest": str(dest),
        "returncode": result["returncode"],
        "error": result["error"],
    }

    evidence = lab.resolve() / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "extract_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (evidence / "extract_stdout.txt").write_text(result["stdout"] or "", encoding="utf-8")
    (evidence / "extract_stderr.txt").write_text(result["stderr"] or "", encoding="utf-8")
    # Make evidence readable by the unprivileged orchestrator that owns the lab.
    for name in ("extract_meta.json", "extract_stdout.txt", "extract_stderr.txt"):
        try:
            os.chmod(evidence / name, 0o644)
        except OSError:
            pass

    print(
        f"privileged_extract: extractor={args.extractor} "
        f"euid={identity['euid']}({identity['user']}) egid={identity['egid']}({identity['group']}) "
        f"rc={result['returncode']}"
    )
    # Exit 0: the helper *ran*; the extractor's own return code lives in meta.
    # A non-zero exit here means a policy refusal only.
    return 0


if __name__ == "__main__":
    sys.exit(main())
