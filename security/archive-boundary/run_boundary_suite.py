#!/usr/bin/env python3
"""Archive-extraction privilege-boundary test orchestrator.

Runs as the *unprivileged* user. For every (test case x extractor) pair it:

  1. Builds a fresh isolated lab: input/ extract/ outside/ evidence/.
  2. Generates the hostile archive for that test (as this unprivileged user).
  3. Snapshots the lab filesystem (before).
  4. Extracts the archive into extract/ via the narrowly scoped sudo helper,
     under strace when available. Only this step uses root.
  5. Snapshots the lab filesystem (after).
  6. Classifies the result SAFE / VULNERABLE / INCONCLUSIVE from the snapshots.
  7. Collects evidence (archive copy, entry metadata, snapshots, diff, strace,
     extractor stdout/stderr, identity) into the output directory.

A post-run guard asserts that nothing escaped the disposable LAB_ROOT. Evidence
is written even when individual tests error, and the process exits non-zero only
if confinement itself was breached.

Classification:
  SAFE         - no archive-controlled write landed outside the extraction root.
  VULNERABLE   - an archive-controlled write landed outside the extraction root
                 (still confined to the disposable lab by construction).
  INCONCLUSIVE - the vector is unsupported by the extractor, or instrumentation
                 was insufficient to decide.
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
import uuid
import zipfile
from pathlib import Path

# Ensure this script's own directory is importable even under `python -I`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostile_zip  # noqa: E402

EXTRACTORS_DEFAULT = (
    "unzip",
    "python-zipfile-extractall",
    "python-zipfile-naive-control",
)

# Whether an extractor materializes symlink entries as real symlinks. The Python
# stdlib does not, so symlink-dependent vectors are "unsupported" for it.
EXTRACTOR_CREATES_SYMLINKS = {
    "unzip": True,
    "python-zipfile-extractall": False,
    "python-zipfile-naive-control": True,
}

EXTRACTOR_LABELS = {
    "unzip": "System Info-ZIP unzip",
    "python-zipfile-extractall": "Python stdlib zipfile.extractall",
    "python-zipfile-naive-control": "Positive control: naive manual join (UNSAFE by design)",
}

# Top-level lab subdirectories that belong to the harness, not to an escape.
HARNESS_DIRS = {"input", "evidence", "extract"}

MAX_HASH_BYTES = 10 * 1024 * 1024

HELPER = str(Path(__file__).resolve().parent / "privileged_extract.py")
WRAPPER = str(Path(__file__).resolve().parent / "privileged_extract_wrapper.sh")


# --------------------------------------------------------------------------- #
# Filesystem snapshotting
# --------------------------------------------------------------------------- #
def _sha256_regular(path: Path) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        h = hashlib.sha256()
        read = 0
        while read < MAX_HASH_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
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
        "inode": lst.st_ino,
        "nlink": lst.st_nlink,
        "size": lst.st_size,
        "setuid": bool(mode & stat.S_ISUID),
        "setgid": bool(mode & stat.S_ISGID),
        "sticky": bool(mode & stat.S_ISVTX),
    }
    if ftype == "symlink":
        try:
            meta["symlink_target"] = os.readlink(path)
        except OSError:
            meta["symlink_target"] = None
        meta["realpath"] = os.path.realpath(path)
    elif ftype == "regular":
        meta["sha256"] = _sha256_regular(path)
    return meta


def snapshot(lab: Path) -> dict:
    lab = Path(lab).resolve()
    out: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(lab, followlinks=False):
        for name in list(dirnames) + list(filenames):
            full = Path(dirpath) / name
            rel = os.path.relpath(full, lab)
            try:
                out[rel] = describe(full)
            except OSError as exc:
                out[rel] = {"type": "error", "error": str(exc)}
    return out


def diff_snapshots(before: dict, after: dict) -> dict:
    added, removed, changed = {}, {}, {}
    for rel, meta in after.items():
        if rel not in before:
            added[rel] = meta
        elif before[rel] != meta:
            changed[rel] = {"before": before[rel], "after": meta}
    for rel, meta in before.items():
        if rel not in after:
            removed[rel] = meta
    return {"added": added, "removed": removed, "changed": changed}


# --------------------------------------------------------------------------- #
# Extraction (the only privileged step)
# --------------------------------------------------------------------------- #
def run_extraction(extractor: str, lab: Path, zip_path: Path, base: Path,
                   use_sudo: bool, strace_path: str | None) -> dict:
    # Everything privileged goes through the single wrapper command so sudo can
    # be narrowly scoped to exactly it. The wrapper runs strace (when present)
    # around the Python helper.
    strace_log = lab / "evidence" / "strace.log"
    cmd: list[str] = []
    if use_sudo:
        cmd += ["sudo", "-n"]
    cmd += [WRAPPER, extractor, str(lab), str(zip_path), str(base)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return {
            "command": cmd,
            "wrapper_returncode": proc.returncode,
            "wrapper_stdout": proc.stdout,
            "wrapper_stderr": proc.stderr,
            "strace_available": bool(strace_path),
            "strace_log": str(strace_log) if strace_path and strace_log.exists() else None,
            "launch_error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "command": cmd,
            "wrapper_returncode": None,
            "wrapper_stdout": "",
            "wrapper_stderr": "",
            "strace_available": bool(strace_path),
            "strace_log": None,
            "launch_error": f"{type(exc).__name__}: {exc}",
        }


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def classify(test_id: str, extractor: str, diff: dict, extract_meta: dict | None,
             after: dict, lab: Path) -> dict:
    added = diff["added"]
    changed = diff["changed"]
    lab = Path(lab).resolve()

    # Escapes: any added/changed object physically living outside the extraction
    # root (i.e. not under input/, evidence/, or extract/).
    escapes = []
    for rel in list(added) + list(changed):
        top = rel.split(os.sep, 1)[0]
        if top in HARNESS_DIRS:
            continue
        meta = after.get(rel, {})
        escapes.append({
            "path": rel,
            "type": meta.get("type"),
            "realpath": os.path.realpath(lab / rel),
            "detail": meta,
        })

    # In-root metadata findings (not boundary escapes, but security-relevant):
    # special files or setuid/setgid/sticky bits under the extraction root.
    metadata_findings = []
    for rel, meta in after.items():
        top = rel.split(os.sep, 1)[0]
        if top != "extract":
            continue
        if meta.get("type") in {"fifo", "char_device", "block_device", "socket"}:
            metadata_findings.append({"path": rel, "finding": f"special file: {meta['type']}", "detail": meta})
        if meta.get("setuid") or meta.get("setgid") or meta.get("sticky"):
            bits = [b for b, k in (("setuid", "setuid"), ("setgid", "setgid"), ("sticky", "sticky")) if meta.get(k)]
            metadata_findings.append({"path": rel, "finding": f"permission bits: {'+'.join(bits)}", "detail": meta})

    extractor_rc = extract_meta.get("returncode") if extract_meta else None
    in_root_writes = [r for r in added if r.split(os.sep, 1)[0] == "extract"]

    needs_symlink = test_id in hostile_zip.VECTOR_REQUIRES_SYMLINK
    extractor_symlinks = EXTRACTOR_CREATES_SYMLINKS.get(extractor, True)

    if escapes:
        classification = "VULNERABLE"
        reason = f"{len(escapes)} archive-controlled write(s) landed outside the extraction root"
    elif needs_symlink and not extractor_symlinks:
        classification = "INCONCLUSIVE"
        reason = ("symlink-dependent vector not applicable: this extractor does not "
                  "materialize symlink entries as symlinks")
    elif extract_meta is None:
        classification = "INCONCLUSIVE"
        reason = "privileged extraction did not run (no extractor metadata recorded)"
    elif extractor_rc not in (0, None) and not in_root_writes and not escapes:
        classification = "INCONCLUSIVE"
        reason = f"extractor exited {extractor_rc} with no observable writes"
    else:
        classification = "SAFE"
        reason = "all writes confined to the extraction root"

    return {
        "classification": classification,
        "reason": reason,
        "escapes": escapes,
        "metadata_findings": metadata_findings,
        "in_root_added_paths": in_root_writes,
    }


# --------------------------------------------------------------------------- #
# Evidence collection
# --------------------------------------------------------------------------- #
def archive_entries(zip_path: Path) -> list[dict]:
    entries = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            mode = (info.external_attr >> 16) & 0xFFFF
            entries.append({
                "name": info.filename,
                "mode_octal": format(mode, "06o"),
                "type": _file_type(mode) if mode else "unspecified",
                "size": info.file_size,
                "crc32": format(info.CRC, "08x"),
                "create_system": info.create_system,
            })
    return entries


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_if_exists(src: Path, dst: Path) -> None:
    try:
        if src.exists():
            shutil.copyfile(src, dst)
    except OSError:
        pass


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Archive-extraction boundary suite.")
    ap.add_argument("--out", required=True, help="evidence output directory (workspace)")
    ap.add_argument("--extractors", nargs="+", default=list(EXTRACTORS_DEFAULT),
                    choices=list(EXTRACTORS_DEFAULT))
    ap.add_argument("--tests", nargs="+", default=list(hostile_zip.TEST_IDS),
                    choices=list(hostile_zip.TEST_IDS))
    ap.add_argument("--base", default=os.environ.get("ARCHIVE_BOUNDARY_BASE")
                    or os.environ.get("RUNNER_TEMP") or "/tmp",
                    help="temp base in which the disposable LAB_ROOT is created")
    ap.add_argument("--no-sudo", action="store_true",
                    help="run the helper directly (no sudo); privilege split not exercised")
    ap.add_argument("--allow-root", action="store_true",
                    help="permit running the orchestrator as root (not recommended)")
    args = ap.parse_args()

    if os.geteuid() == 0 and not args.allow_root:
        print("run_boundary_suite: refusing to orchestrate as root; "
              "generation/orchestration must be unprivileged. Use --allow-root to override.",
              file=sys.stderr)
        return 2

    base = Path(args.base).resolve()
    base.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    strace_path = shutil.which("strace")
    use_sudo = not args.no_sudo and shutil.which("sudo") is not None

    lab_root = Path(tempfile.mkdtemp(prefix="archbound-", dir=base))
    canary = lab_root / "_CANARY_DO_NOT_TOUCH"
    canary.write_text("confinement canary\n", encoding="utf-8")
    canary_hash = sha256_file(canary)
    expected_top = {canary.name}

    print(f"LAB_ROOT: {lab_root}")
    print(f"evidence out: {out_dir}")
    print(f"sudo: {use_sudo}   strace: {strace_path or 'unavailable'}")

    results = []
    guard_breaches = []

    for test_id in args.tests:
        for extractor in args.extractors:
            lab_name = f"{test_id}__{extractor}"
            expected_top.add(lab_name)
            lab = lab_root / lab_name
            for sub in ("input", "extract", "outside", "evidence"):
                (lab / sub).mkdir(parents=True, exist_ok=True)

            tag = uuid.uuid4().hex[:8]
            case_out = out_dir / lab_name
            case_out.mkdir(parents=True, exist_ok=True)

            record = {
                "test_id": test_id,
                "title": hostile_zip.TEST_TITLES[test_id],
                "extractor": extractor,
                "extractor_label": EXTRACTOR_LABELS[extractor],
                "lab": str(lab),
                "tag": tag,
            }

            try:
                zip_path = hostile_zip.build(lab, test_id, tag)
            except AssertionError as exc:
                record["classification"] = "INCONCLUSIVE"
                record["reason"] = f"archive generation refused by safety gate: {exc}"
                write_json(case_out / "result.json", record)
                results.append(record)
                print(f"[{lab_name}] GENERATION REFUSED: {exc}")
                continue

            record["archive_sha256"] = sha256_file(zip_path)
            record["archive_entries"] = archive_entries(zip_path)
            copy_if_exists(zip_path, case_out / "payload.zip")
            write_json(case_out / "archive_entries.json", record["archive_entries"])

            before = snapshot(lab)
            write_json(case_out / "snapshot_before.json", before)

            extraction = run_extraction(extractor, lab, zip_path, base, use_sudo, strace_path)

            after = snapshot(lab)
            write_json(case_out / "snapshot_after.json", after)
            diff = diff_snapshots(before, after)
            write_json(case_out / "diff.json", diff)

            # Extractor-recorded identity / metadata, written by the privileged helper.
            extract_meta = None
            meta_path = lab / "evidence" / "extract_meta.json"
            if meta_path.exists():
                try:
                    extract_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    extract_meta = None

            for fn in ("extract_meta.json", "extract_stdout.txt", "extract_stderr.txt", "strace.log"):
                copy_if_exists(lab / "evidence" / fn, case_out / fn)

            verdict = classify(test_id, extractor, diff, extract_meta, after, lab)

            record.update({
                "identity": (extract_meta or {}).get("identity"),
                "extractor_version": (extract_meta or {}).get("extractor_version"),
                "extractor_returncode": (extract_meta or {}).get("returncode"),
                "extractor_error": (extract_meta or {}).get("error"),
                "wrapper_returncode": extraction["wrapper_returncode"],
                "wrapper_stderr": extraction["wrapper_stderr"][-2000:],
                "launch_error": extraction["launch_error"],
                "strace_available": extraction["strace_available"],
                "strace_log_present": extraction["strace_log"] is not None,
                "classification": verdict["classification"],
                "reason": verdict["reason"],
                "escapes": verdict["escapes"],
                "metadata_findings": verdict["metadata_findings"],
                "in_root_added_paths": verdict["in_root_added_paths"],
            })
            write_json(case_out / "result.json", record)
            results.append(record)

            # Immediate per-test confinement guard: nothing new at LAB_ROOT top
            # level except the known labs and canary.
            for entry in os.listdir(lab_root):
                if entry not in expected_top:
                    guard_breaches.append({"stage": lab_name, "unexpected_lab_root_entry": entry})

            idbit = ""
            if record["identity"]:
                idbit = (f" euid={record['identity']['euid']}"
                         f"({record['identity']['user']})"
                         f" gid={record['identity']['egid']}")
            print(f"[{lab_name}] {verdict['classification']:12s} rc="
                  f"{record['extractor_returncode']}{idbit} :: {verdict['reason']}")

    # Final confinement guard.
    if canary.exists() and sha256_file(canary) == canary_hash:
        canary_ok = True
    else:
        canary_ok = False
        guard_breaches.append({"stage": "final", "canary": "missing or modified"})
    for entry in os.listdir(lab_root):
        if entry not in expected_top:
            guard_breaches.append({"stage": "final", "unexpected_lab_root_entry": entry})

    counts: dict[str, int] = {}
    for r in results:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1

    summary = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lab_root": str(lab_root),
        "base": str(base),
        "sudo_used": use_sudo,
        "strace_available": bool(strace_path),
        "extractors": args.extractors,
        "tests": args.tests,
        "counts": counts,
        "canary_ok": canary_ok,
        "confinement_guard_breaches": guard_breaches,
        "results": results,
    }
    write_json(out_dir / "summary.json", summary)
    write_markdown(out_dir / "summary.md", summary)

    print("\n=== SUMMARY ===")
    print(json.dumps(counts, indent=2))
    print(f"canary_ok={canary_ok} guard_breaches={len(guard_breaches)}")

    # Best-effort cleanup of the disposable lab (evidence already mirrored to out).
    try:
        shutil.rmtree(lab_root, ignore_errors=False)
    except OSError:
        # Root-owned extracted files may resist unprivileged rmtree; that's fine,
        # the lab is in ephemeral temp and the runner is torn down after the job.
        shutil.rmtree(lab_root, ignore_errors=True)

    if guard_breaches:
        print("CONFINEMENT GUARD BREACH DETECTED", file=sys.stderr)
        return 3
    return 0


def write_markdown(path: Path, summary: dict) -> None:
    lines = [
        "# Archive-extraction boundary suite — results",
        "",
        f"- Generated (UTC): `{summary['generated_utc']}`",
        f"- LAB_ROOT: `{summary['lab_root']}`",
        f"- sudo used for extraction: `{summary['sudo_used']}`",
        f"- strace available: `{summary['strace_available']}`",
        f"- Confinement canary intact: `{summary['canary_ok']}`",
        f"- Guard breaches: `{len(summary['confinement_guard_breaches'])}`",
        "",
        "## Classification counts",
        "",
        "```",
        json.dumps(summary["counts"], indent=2),
        "```",
        "",
        "## Results",
        "",
        "| Test | Extractor | Result | Extractor rc | euid | Reason |",
        "|------|-----------|--------|--------------|------|--------|",
    ]
    for r in summary["results"]:
        ident = r.get("identity") or {}
        euid = ident.get("euid", "?")
        reason = r.get("reason", "").replace("|", "\\|")
        lines.append(
            f"| {r['test_id']} | {r['extractor']} | {r['classification']} | "
            f"{r.get('extractor_returncode')} | {euid} | {reason} |"
        )
    lines += [
        "",
        "## Test case legend",
        "",
    ]
    for tid in hostile_zip.TEST_IDS:
        lines.append(f"- **{tid}** — {hostile_zip.TEST_TITLES[tid]}")
    lines.append("")
    lines.append("> VULNERABLE findings are archive-controlled writes outside the extraction")
    lines.append("> root; by construction they remain confined to the disposable lab directory.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
