#!/usr/bin/env python3
"""T9 privileged extraction helper — invoked only via the sudo wrapper.

Runs as root. It:
  1. Validates that the lab is a disposable 'archbound-' directory under the
     temp base and that no path component is a symlink.
  2. Creates <lab>/dest with the requested owner / group / mode (chown BEFORE
     chmod so the setgid bit survives chown).
  3. Extracts one archive into dest either as root, or — for the unprivileged
     case — in a child that drops to the target uid/gid first.
  4. Records the extractor's effective identity, version, umask, return code and
     stderr, plus a description of the destination it created.

It performs no other privileged action. It does NOT sanitize archive contents
(not needed: the T9 payload is a single benign relative entry) and it does not
restore ownership from the archive (ZIP carries none). Confinement rests on the
benign single-entry payload plus the orchestrator's post-run guard.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ALLOWED_EXTRACTORS = ("unzip", "python-zipfile-extractall")

_ID_SNIPPET = (
    "import os,json;"
    "print(json.dumps({'euid':os.geteuid(),'egid':os.getegid(),"
    "'ruid':os.getuid(),'rgid':os.getgid(),'groups':sorted(os.getgroups())}))"
)
_EXTRACT_SNIPPET = (
    "import zipfile,sys;"
    "zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])"
)

UMASK = 0o022


def _fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"provenance_extract_helper: REFUSED: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _allowed_base() -> Path:
    base = os.environ.get("ARCHIVE_BOUNDARY_BASE") or os.environ.get("RUNNER_TEMP") or "/tmp"
    return Path(base).resolve()


def _has_symlink_component(path: Path, stop: Path) -> bool:
    path = Path(os.path.normpath(str(path)))
    cur = stop
    for part in path.relative_to(stop).parts:
        cur = cur / part
        if os.path.islink(cur):
            return True
    return False


def _validate(lab: Path, zip_path: Path) -> None:
    base = _allowed_base()
    lab_real = Path(os.path.realpath(lab))
    if not (str(lab_real) == str(base) or str(lab_real).startswith(str(base) + os.sep)):
        _fail(f"lab {lab_real} is not under allowed base {base}")
    if "archbound-" not in str(lab_real):
        _fail(f"lab {lab_real} lacks the 'archbound-' marker")
    if not lab_real.is_dir():
        _fail(f"lab {lab_real} is not a directory")
    if _has_symlink_component(lab_real, base):
        _fail(f"a path component of {lab_real} is a symlink")
    st = os.lstat(zip_path)
    if not stat.S_ISREG(st.st_mode):
        _fail(f"archive {zip_path} is not a regular file")


def _setup_destination(lab: Path, owner: int, group: int, mode: int) -> dict:
    dest = lab / "dest"
    if dest.exists():
        _fail(f"destination already exists: {dest}")
    os.mkdir(dest, 0o700)
    # chown first — chown clears setuid/setgid bits, so chmod must follow.
    os.chown(dest, owner, group)
    os.chmod(dest, mode)
    final = os.lstat(dest)
    return {
        "path": str(dest),
        "requested_owner": owner,
        "requested_group": group,
        "requested_mode_octal": format(mode, "04o"),
        "actual_uid": final.st_uid,
        "actual_gid": final.st_gid,
        "actual_mode_octal": format(stat.S_IMODE(final.st_mode), "04o"),
        "setgid": bool(final.st_mode & stat.S_ISGID),
    }


def _preexec(as_uid: int, as_gid: int):
    def fn():
        if as_uid != 0:
            os.setgroups([as_gid])
            os.setgid(as_gid)
            os.setuid(as_uid)
        os.umask(UMASK)
    return fn


def _run(cmd: list[str], as_uid: int, as_gid: int, timeout: int = 60) -> subprocess.CompletedProcess:
    with open(os.devnull, "rb") as devnull:
        return subprocess.run(
            cmd, stdin=devnull, capture_output=True, text=True,
            timeout=timeout, preexec_fn=_preexec(as_uid, as_gid),
        )


def _capture_identity(as_uid: int, as_gid: int) -> dict:
    try:
        proc = _run([sys.executable, "-I", "-c", _ID_SNIPPET], as_uid, as_gid, timeout=20)
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip().splitlines()[-1])
            data["user"] = _name(as_uid, is_group=False)
            data["group"] = _name(as_gid, is_group=True)
            data["umask"] = format(UMASK, "04o")
            return data
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"error": "identity capture failed"}


def _name(ident: int, is_group: bool) -> str:
    try:
        if is_group:
            import grp
            return grp.getgrgid(ident).gr_name
        import pwd
        return pwd.getpwuid(ident).pw_name
    except (KeyError, ImportError):
        return str(ident)


def _extractor_version(extractor: str) -> str:
    if extractor == "unzip":
        try:
            out = subprocess.run(["unzip", "-v"], capture_output=True, text=True, timeout=20).stdout
            return out.splitlines()[0].strip() if out else "unzip (unknown)"
        except Exception as exc:  # noqa: BLE001
            return f"unzip (version query failed: {exc})"
    return f"python {sys.version.split()[0]} zipfile"


def _extract(extractor: str, zip_path: Path, dest: Path, as_uid: int, as_gid: int) -> dict:
    if extractor == "unzip":
        cmd = ["unzip", "-q", str(zip_path), "-d", str(dest)]
    else:
        cmd = [sys.executable, "-I", "-c", _EXTRACT_SNIPPET, str(zip_path), str(dest)]
    try:
        proc = _run(cmd, as_uid, as_gid, timeout=60)
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"returncode": 1, "stdout": "", "stderr": "", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser(description="T9 privileged extraction helper.")
    ap.add_argument("--lab", required=True)
    ap.add_argument("--zip", required=True, dest="zip_path")
    ap.add_argument("--extractor", required=True, choices=ALLOWED_EXTRACTORS)
    ap.add_argument("--entry", required=True)
    ap.add_argument("--dest-owner", type=int, required=True)
    ap.add_argument("--dest-group", type=int, required=True)
    ap.add_argument("--dest-mode", required=True, help="octal, e.g. 2755")
    ap.add_argument("--as-uid", type=int, required=True)
    ap.add_argument("--as-gid", type=int, required=True)
    args = ap.parse_args()

    lab = Path(args.lab)
    zip_path = Path(args.zip_path)
    _validate(lab, zip_path)

    try:
        mode = int(args.dest_mode, 8)
    except ValueError:
        _fail(f"invalid octal dest-mode: {args.dest_mode!r}")

    setup = _setup_destination(lab, args.dest_owner, args.dest_group, mode)
    dest = Path(setup["path"])

    identity = _capture_identity(args.as_uid, args.as_gid)
    result = _extract(args.extractor, zip_path, dest, args.as_uid, args.as_gid)

    meta = {
        "extractor": args.extractor,
        "extractor_version": _extractor_version(args.extractor),
        "entry": args.entry,
        "helper_euid": os.geteuid(),
        "helper_egid": os.getegid(),
        "requested_extract_as": {"uid": args.as_uid, "gid": args.as_gid},
        "extractor_identity": identity,
        "umask": format(UMASK, "04o"),
        "setup": setup,
        "extractor_returncode": result["returncode"],
        "extractor_error": result["error"],
        "extractor_stderr": result["stderr"],
        "refusal": None,
    }

    evidence = lab.resolve() / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "extract_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (evidence / "extract_stdout.txt").write_text(result["stdout"] or "", encoding="utf-8")
    (evidence / "extract_stderr.txt").write_text(result["stderr"] or "", encoding="utf-8")
    for name in ("extract_meta.json", "extract_stdout.txt", "extract_stderr.txt"):
        try:
            os.chmod(evidence / name, 0o644)
        except OSError:
            pass

    print(
        f"provenance_extract_helper: dest={setup['actual_uid']}:{setup['actual_gid']} "
        f"mode={setup['actual_mode_octal']} extract_as={args.as_uid}:{args.as_gid} "
        f"extractor={args.extractor} rc={result['returncode']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
