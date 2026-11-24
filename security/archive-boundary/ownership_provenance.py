#!/usr/bin/env python3
"""T9 — ownership provenance experiment.

Question
--------
A historical copy of the artifact ``screenshots/01_github.com_01.png`` carried
the on-disk ownership signature **UID 0 : GID 1001 : mode 0644**. ZIP archives do
not transport POSIX uid/gid (Info-ZIP ``unzip`` without ``-X`` and Python
``zipfile`` both ignore ownership), so that signature cannot have come from the
archive. This experiment determines whether — and under exactly which
conditions — the signature is reproduced by the *extraction environment*,
specifically by extracting as root into a **setgid** destination directory whose
group is 1001.

This is a neutral test of POSIX extraction mechanics. It does NOT reconstruct any
specific product's internal extraction path, and it makes no claim about how the
original file was produced. It reports only whether the ownership signature is
reproducible and which conditions are necessary.

Method
------
For every (destination profile x extracting identity x extractor):
  * The unprivileged orchestrator builds a ZIP holding a single entry
    ``screenshots/01_github.com_01.png`` (mode 0644, synthetic stand-in bytes).
  * A fresh disposable lab is created under the temp base.
  * The narrowly scoped sudo helper (root) creates the destination directory with
    the exact owner/group/mode, then performs the extraction either as root or,
    for the unprivileged case, after dropping to uid 1000 / gid 1000.
  * The orchestrator snapshots before/after, records archive metadata, extractor
    identity, and the resulting file's ownership, mode, inode, timestamps and
    SHA-256, then decides whether UID 0 : GID 1001 : 0644 was reproduced.

Destination profiles:
  A: owner 0,    group 1001, mode 2755 (setgid)
  B: owner 1000, group 1001, mode 2755 (setgid)
  C: owner 1000, group 1000, mode 0755 (no setgid)

Extracting identities: root (uid 0), unprivileged (uid 1000 / gid 1000).

The original artifact is never touched (it is not in the repository); all
experiments live in disposable temp directories. The orchestrator exits non-zero
only if confinement is breached.
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
import tempfile
import time
import zipfile
from pathlib import Path

ENTRY_PATH = "screenshots/01_github.com_01.png"

# Reference identity of the ORIGINAL artifact (as given). Content is not
# reproduced here; ownership/mode is the subject of the experiment.
REFERENCE = {
    "path": ENTRY_PATH,
    "sha256": "dd0ff1aa6371838a1bd9d941b4b43a1596a01974c202a6967416f20916f61f51",
    "uid": 0,
    "gid": 1001,
    "mode_octal": "0644",
    "note": "historical on-disk ownership signature of the original PNG",
}

DESTINATIONS = {
    "A": {"owner": 0, "group": 1001, "mode": 0o2755, "desc": "owner 0, group 1001, mode 2755 (setgid)"},
    "B": {"owner": 1000, "group": 1001, "mode": 0o2755, "desc": "owner 1000, group 1001, mode 2755 (setgid)"},
    "C": {"owner": 1000, "group": 1000, "mode": 0o0755, "desc": "owner 1000, group 1000, mode 0755 (no setgid)"},
}

IDENTITIES = {
    "root": {"uid": 0, "gid": 0},
    "unpriv": {"uid": 1000, "gid": 1000},
}

EXTRACTORS = ("unzip", "python-zipfile-extractall")

# Minimal valid 1x1 PNG used as a stand-in payload (ownership is independent of
# content; the original bytes with the reference SHA-256 are not in the repo).
_PNG_STANDIN = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)

HELPER = str(Path(__file__).resolve().parent / "provenance_extract_helper.py")
WRAPPER = str(Path(__file__).resolve().parent / "provenance_extract_wrapper.sh")


# --------------------------------------------------------------------------- #
# Metadata helpers (self-contained so T9 does not couple to the T1-T8 suite)
# --------------------------------------------------------------------------- #
def _sha256_regular(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        h = hashlib.sha256()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            h.update(chunk)
        return h.hexdigest()
    finally:
        os.close(fd)


def _file_type(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISCHR(mode):
        return "char_device"
    if stat.S_ISBLK(mode):
        return "block_device"
    if stat.S_ISSOCK(mode):
        return "socket"
    return "other"


def _name_for_uid(uid: int) -> str:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (KeyError, ImportError):
        return str(uid)


def _name_for_gid(gid: int) -> str:
    try:
        import grp
        return grp.getgrgid(gid).gr_name
    except (KeyError, ImportError):
        return str(gid)


def _ts(ns: int) -> str:
    import datetime
    sec, nano = divmod(ns, 1_000_000_000)
    whole = datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc)
    return whole.strftime("%Y-%m-%dT%H:%M:%S") + f".{nano:09d}Z"


def describe(path: Path) -> dict:
    lst = os.lstat(path)
    mode = lst.st_mode
    ftype = _file_type(mode)
    meta = {
        "type": ftype,
        "perm_octal": format(stat.S_IMODE(mode), "04o"),
        "mode_full_octal": format(mode, "06o"),
        "uid": lst.st_uid,
        "gid": lst.st_gid,
        "user": _name_for_uid(lst.st_uid),
        "group": _name_for_gid(lst.st_gid),
        "inode": lst.st_ino,
        "nlink": lst.st_nlink,
        "size": lst.st_size,
        "setuid": bool(mode & stat.S_ISUID),
        "setgid": bool(mode & stat.S_ISGID),
        "sticky": bool(mode & stat.S_ISVTX),
        "atime_utc": _ts(lst.st_atime_ns),
        "mtime_utc": _ts(lst.st_mtime_ns),
        "ctime_utc": _ts(lst.st_ctime_ns),
    }
    if ftype == "symlink":
        try:
            meta["symlink_target"] = os.readlink(path)
        except OSError:
            meta["symlink_target"] = None
    elif ftype == "regular":
        meta["sha256"] = _sha256_regular(path)
    return meta


def snapshot(root: Path) -> dict:
    root = Path(root).resolve()
    out: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames) + list(filenames):
            full = Path(dirpath) / name
            rel = os.path.relpath(full, root)
            try:
                out[rel] = describe(full)
            except OSError as exc:
                out[rel] = {"type": "error", "error": str(exc)}
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_if_exists(src: Path, dst: Path) -> None:
    try:
        if src.exists():
            shutil.copyfile(src, dst)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Archive build (unprivileged)
# --------------------------------------------------------------------------- #
def build_archive(lab: Path) -> Path:
    input_dir = lab / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    zip_path = input_dir / "payload.zip"
    info = zipfile.ZipInfo(ENTRY_PATH)
    info.create_system = 3  # Unix
    info.external_attr = (0o100644 & 0xFFFF) << 16  # regular file, mode 0644
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(info, _PNG_STANDIN)
    os.chmod(zip_path, 0o644)
    return zip_path


def archive_entries(zip_path: Path) -> list[dict]:
    entries = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            mode = (info.external_attr >> 16) & 0xFFFF
            entries.append({
                "name": info.filename,
                "mode_octal": format(mode, "06o"),
                "size": info.file_size,
                "crc32": format(info.CRC, "08x"),
                "create_system": info.create_system,
            })
    return entries


# --------------------------------------------------------------------------- #
# Extraction (via the narrowly scoped sudo helper)
# --------------------------------------------------------------------------- #
def run_case(lab: Path, base: Path, dest_profile: dict, identity: dict,
             extractor: str, zip_path: Path, use_sudo: bool) -> dict:
    cmd: list[str] = []
    if use_sudo:
        cmd += ["sudo", "-n"]
    cmd += [
        WRAPPER, str(base),
        "--lab", str(lab),
        "--zip", str(zip_path),
        "--extractor", extractor,
        "--entry", ENTRY_PATH,
        "--dest-owner", str(dest_profile["owner"]),
        "--dest-group", str(dest_profile["group"]),
        "--dest-mode", format(dest_profile["mode"], "o"),
        "--as-uid", str(identity["uid"]),
        "--as-gid", str(identity["gid"]),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return {
            "command": cmd,
            "wrapper_returncode": proc.returncode,
            "wrapper_stdout": proc.stdout,
            "wrapper_stderr": proc.stderr,
            "launch_error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "command": cmd,
            "wrapper_returncode": None,
            "wrapper_stdout": "",
            "wrapper_stderr": "",
            "launch_error": f"{type(exc).__name__}: {exc}",
        }


def analyze(lab: Path, extract_meta: dict | None) -> dict:
    dest = lab / "dest"
    png = dest / ENTRY_PATH
    parent = png.parent

    result_file = describe(png) if png.exists() or png.is_symlink() else None
    result_parent = describe(parent) if parent.exists() else None
    result_dest = describe(dest) if dest.exists() else None

    extractor_rc = (extract_meta or {}).get("extractor_returncode")
    extractor_err = (extract_meta or {}).get("extractor_error")

    if result_file is None:
        reproduced = False
        if extractor_rc not in (0, None):
            status = "NOT_APPLICABLE"
            stderr_lines = ((extract_meta or {}).get("extractor_stderr", "") or "").strip().splitlines()
            hint = stderr_lines[-1].strip() if stderr_lines else "destination not writable by this identity"
            reason = (f"extraction did not create the file (rc={extractor_rc}): {hint}")
        else:
            status = "INCONCLUSIVE"
            reason = "extraction reported success but the expected file is absent"
        field_match = {}
    else:
        uid_ok = result_file["uid"] == REFERENCE["uid"]
        gid_ok = result_file["gid"] == REFERENCE["gid"]
        mode_ok = result_file["perm_octal"] == "0644"
        field_match = {"uid": uid_ok, "gid": gid_ok, "mode": mode_ok}
        reproduced = uid_ok and gid_ok and mode_ok
        status = "REPRODUCED" if reproduced else "NOT_REPRODUCED"
        reason = (
            f"result uid={result_file['uid']} gid={result_file['gid']} "
            f"mode={result_file['perm_octal']} vs reference "
            f"uid={REFERENCE['uid']} gid={REFERENCE['gid']} mode={REFERENCE['mode_octal']}"
        )

    return {
        "status": status,
        "reproduced_signature": reproduced,
        "reason": reason,
        "field_match": field_match,
        "result_file": result_file,
        "result_parent_dir": result_parent,
        "result_dest_dir": result_dest,
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="T9 ownership provenance experiment.")
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default=os.environ.get("ARCHIVE_BOUNDARY_BASE")
                    or os.environ.get("RUNNER_TEMP") or "/tmp")
    ap.add_argument("--dests", nargs="+", default=list(DESTINATIONS), choices=list(DESTINATIONS))
    ap.add_argument("--identities", nargs="+", default=list(IDENTITIES), choices=list(IDENTITIES))
    ap.add_argument("--extractors", nargs="+", default=list(EXTRACTORS), choices=list(EXTRACTORS))
    ap.add_argument("--no-sudo", action="store_true")
    ap.add_argument("--allow-root", action="store_true",
                    help="permit running the orchestrator as root (generation should be unprivileged)")
    args = ap.parse_args()

    if os.geteuid() == 0 and not args.allow_root:
        print("ownership_provenance: refusing to orchestrate as root; use --allow-root to override.",
              file=sys.stderr)
        return 2

    base = Path(args.base).resolve()
    base.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    use_sudo = not args.no_sudo and shutil.which("sudo") is not None

    lab_root = Path(tempfile.mkdtemp(prefix="archbound-t9-", dir=base))
    canary = lab_root / "_CANARY_DO_NOT_TOUCH"
    canary.write_text("confinement canary\n", encoding="utf-8")
    canary_hash = sha256_file(canary)
    expected_top = {canary.name}

    # Let a dropped unprivileged extractor TRAVERSE into the labs (x for others),
    # so each destination's own permissions — not LAB_ROOT's default 0700 — gate
    # the write. Others still cannot list LAB_ROOT (no r bit).
    try:
        os.chmod(lab_root, 0o711)
    except OSError:
        pass

    print(f"LAB_ROOT: {lab_root}")
    print(f"evidence out: {out_dir}")
    print(f"sudo: {use_sudo}")
    print(f"reference signature: uid={REFERENCE['uid']} gid={REFERENCE['gid']} mode={REFERENCE['mode_octal']}")

    results = []
    guard_breaches = []

    for dest_key in args.dests:
        for id_key in args.identities:
            for extractor in args.extractors:
                dest_profile = DESTINATIONS[dest_key]
                identity = IDENTITIES[id_key]
                case_name = f"T9-{dest_key}-{id_key}-{extractor}"
                expected_top.add(case_name)
                lab = lab_root / case_name
                (lab / "input").mkdir(parents=True, exist_ok=True)
                (lab / "evidence").mkdir(parents=True, exist_ok=True)

                case_out = out_dir / case_name
                case_out.mkdir(parents=True, exist_ok=True)

                record = {
                    "case": case_name,
                    "destination": dest_key,
                    "destination_profile": {
                        "owner": dest_profile["owner"],
                        "group": dest_profile["group"],
                        "mode_octal": format(dest_profile["mode"], "04o"),
                        "setgid": bool(dest_profile["mode"] & stat.S_ISGID),
                        "description": dest_profile["desc"],
                    },
                    "extracting_identity": id_key,
                    "extracting_uid": identity["uid"],
                    "extracting_gid": identity["gid"],
                    "extractor": extractor,
                    "reference": REFERENCE,
                    "lab": str(lab),
                }

                zip_path = build_archive(lab)
                record["archive_sha256"] = sha256_file(zip_path)
                record["archive_entries"] = archive_entries(zip_path)
                copy_if_exists(zip_path, case_out / "payload.zip")
                write_json(case_out / "archive_entries.json", record["archive_entries"])

                before = snapshot(lab)
                write_json(case_out / "snapshot_before.json", before)

                run = run_case(lab, base, dest_profile, identity, extractor, zip_path, use_sudo)

                after = snapshot(lab)
                write_json(case_out / "snapshot_after.json", after)

                extract_meta = None
                meta_path = lab / "evidence" / "extract_meta.json"
                if meta_path.exists():
                    try:
                        extract_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        extract_meta = None
                for fn in ("extract_meta.json", "extract_stdout.txt", "extract_stderr.txt"):
                    copy_if_exists(lab / "evidence" / fn, case_out / fn)

                verdict = analyze(lab, extract_meta)

                record.update({
                    "setup": (extract_meta or {}).get("setup"),
                    "extractor_identity": (extract_meta or {}).get("extractor_identity"),
                    "extractor_version": (extract_meta or {}).get("extractor_version"),
                    "umask": (extract_meta or {}).get("umask"),
                    "extractor_returncode": (extract_meta or {}).get("extractor_returncode"),
                    "extractor_error": (extract_meta or {}).get("extractor_error"),
                    "extractor_stderr": (extract_meta or {}).get("extractor_stderr", "")[-1500:],
                    "helper_refusal": (extract_meta or {}).get("refusal"),
                    "wrapper_returncode": run["wrapper_returncode"],
                    "wrapper_stderr": run["wrapper_stderr"][-1500:],
                    "launch_error": run["launch_error"],
                    "status": verdict["status"],
                    "reproduced_signature": verdict["reproduced_signature"],
                    "reason": verdict["reason"],
                    "field_match": verdict["field_match"],
                    "result_file": verdict["result_file"],
                    "result_parent_dir": verdict["result_parent_dir"],
                    "result_dest_dir": verdict["result_dest_dir"],
                })
                write_json(case_out / "result.json", record)
                results.append(record)

                for entry in os.listdir(lab_root):
                    if entry not in expected_top:
                        guard_breaches.append({"stage": case_name, "unexpected_lab_root_entry": entry})

                rf = verdict["result_file"]
                owned = f"uid={rf['uid']} gid={rf['gid']} mode={rf['perm_octal']}" if rf else "no file"
                print(f"[{case_name}] {verdict['status']:15s} {owned} :: {verdict['reason']}")

    # Final confinement guard.
    canary_ok = canary.exists() and sha256_file(canary) == canary_hash
    if not canary_ok:
        guard_breaches.append({"stage": "final", "canary": "missing or modified"})
    for entry in os.listdir(lab_root):
        if entry not in expected_top:
            guard_breaches.append({"stage": "final", "unexpected_lab_root_entry": entry})

    reproduced = [r["case"] for r in results if r["reproduced_signature"]]
    necessary = derive_conditions(results)
    observations = build_observations(results)

    summary = {
        "experiment": "T9 ownership provenance",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lab_root": str(lab_root),
        "base": str(base),
        "sudo_used": use_sudo,
        "reference": REFERENCE,
        "zip_transports_ownership": False,
        "zip_ownership_note": (
            "ZIP does not transport POSIX uid/gid; Info-ZIP unzip (without -X) and "
            "Python zipfile ignore ownership, so any uid/gid on the extracted file "
            "comes from the extraction process and destination directory, not the archive."
        ),
        "cases_total": len(results),
        "cases_reproduced": reproduced,
        "necessary_conditions": necessary,
        "observations": observations,
        "canary_ok": canary_ok,
        "confinement_guard_breaches": guard_breaches,
        "disclaimer": (
            "This experiment characterizes POSIX extraction mechanics only. It does "
            "not reconstruct any product's internal extraction path and makes no claim "
            "about how the original artifact was produced."
        ),
        "results": results,
    }
    write_json(out_dir / "summary.json", summary)
    write_markdown(out_dir / "summary.md", summary)

    print("\n=== T9 SUMMARY ===")
    print(f"reproduced: {reproduced or 'none'}")
    print(f"canary_ok={canary_ok} guard_breaches={len(guard_breaches)}")

    try:
        shutil.rmtree(lab_root)
    except OSError:
        shutil.rmtree(lab_root, ignore_errors=True)

    if guard_breaches:
        print("CONFINEMENT GUARD BREACH DETECTED", file=sys.stderr)
        return 3
    return 0


def derive_conditions(results: list[dict]) -> dict:
    """Summarize which conditions were present in reproducing vs non-reproducing cases."""
    repro = [r for r in results if r["reproduced_signature"]]
    conditions = []
    if repro:
        all_root = all(r["extracting_uid"] == 0 for r in repro)
        all_setgid = all(r["destination_profile"]["setgid"] for r in repro)
        all_grp = all(r["destination_profile"]["group"] == 1001 for r in repro)
        if all_root:
            conditions.append("extraction performed as root (yields file UID 0)")
        if all_setgid and all_grp:
            conditions.append("destination directory is setgid with group 1001 (file inherits GID 1001)")
        conditions.append("resulting file mode 0644 (umask 022 / archive mode)")
    return {
        "reproducible": bool(repro),
        "reproducing_cases": [r["case"] for r in repro],
        "conditions": conditions,
    }


def build_observations(results: list[dict]) -> list[str]:
    obs = []
    for r in results:
        rf = r.get("result_file")
        if rf and r["extracting_uid"] != 0 and rf["gid"] == 1001 and rf["uid"] != 0:
            obs.append(
                f"GID 1001 is reproducible WITHOUT root via setgid inheritance "
                f"(e.g. {r['case']}: uid={rf['uid']} gid={rf['gid']}), but UID 0 requires root."
            )
            break
    for r in results:
        rf = r.get("result_file")
        if rf and r["extracting_uid"] == 0 and not r["destination_profile"]["setgid"] and rf["gid"] != 1001:
            obs.append(
                f"Without a setgid destination of group 1001, root extraction yields the process "
                f"GID ({rf['gid']}), not 1001 (e.g. {r['case']})."
            )
            break
    obs.append(
        "ZIP carries no POSIX uid/gid, so the reproduced GID 1001 originates from the setgid "
        "destination directory, not the archive."
    )
    return obs


def write_markdown(path: Path, summary: dict) -> None:
    ref = summary["reference"]
    lines = [
        "# T9 — ownership provenance: results",
        "",
        f"- Generated (UTC): `{summary['generated_utc']}`",
        f"- Reference signature: **UID {ref['uid']} : GID {ref['gid']} : mode {ref['mode_octal']}** "
        f"(original `{ref['path']}`, sha256 `{ref['sha256']}`)",
        f"- ZIP transports POSIX uid/gid: **{summary['zip_transports_ownership']}** — "
        "uid/gid on the extracted file come from the extraction environment, not the archive.",
        f"- Confinement canary intact: `{summary['canary_ok']}`; guard breaches: "
        f"`{len(summary['confinement_guard_breaches'])}`",
        "",
        "## Reproduced in",
        "",
        ("- " + ", ".join(summary["cases_reproduced"])) if summary["cases_reproduced"] else "- (none)",
        "",
        "## Necessary conditions",
        "",
    ]
    for c in summary["necessary_conditions"]["conditions"]:
        lines.append(f"- {c}")
    if not summary["necessary_conditions"]["conditions"]:
        lines.append("- (signature not reproduced in any tested case)")
    lines += ["", "## Observations", ""]
    for o in summary.get("observations", []):
        lines.append(f"- {o}")
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Destination | Extract as | Extractor | Result uid:gid:mode | Status |",
        "|------|-------------|-----------|-----------|---------------------|--------|",
    ]
    for r in summary["results"]:
        rf = r.get("result_file")
        owned = f"{rf['uid']}:{rf['gid']}:{rf['perm_octal']}" if rf else "— (no file)"
        dp = r["destination_profile"]
        lines.append(
            f"| {r['destination']} | {dp['owner']}:{dp['group']} {dp['mode_octal']} | "
            f"{r['extracting_identity']} ({r['extracting_uid']}) | {r['extractor']} | "
            f"{owned} | {r['status']} |"
        )
    lines += [
        "",
        f"> {summary['disclaimer']}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
