#!/usr/bin/env python3
"""T15: isolated ZIP extraction tests against pre-existing symlinks.

All writes stay under a fresh sandbox directory. The canary is outside the
extract root but inside the sandbox, so an escape is observable and harmless.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

PAYLOAD = b"T15_PREEXISTING_SYMLINK_REDIRECTION_CANARY\n"
MARKER = b"CANARY_MARKER_ORIGINAL\n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_case(base: Path, scenario: str) -> tuple[Path, Path, Path]:
    dest = base / "extract"
    canary = base / "canary"
    dest.mkdir(parents=True)
    canary.mkdir()
    (canary / "marker.txt").write_bytes(MARKER)

    if scenario == "direct-directory":
        (dest / "screenshots").symlink_to("../canary")
        member = "screenshots/probe.txt"
        external = canary / "probe.txt"
    elif scenario == "nested-directory":
        screenshots = dest / "screenshots"
        screenshots.mkdir()
        (screenshots / "cache").symlink_to("../../canary")
        member = "screenshots/cache/probe.txt"
        external = canary / "probe.txt"
    elif scenario == "symlink-chain":
        (base / "bridge").symlink_to("canary")
        (dest / "screenshots").symlink_to("../bridge")
        member = "screenshots/probe.txt"
        external = canary / "probe.txt"
    elif scenario == "final-file":
        screenshots = dest / "screenshots"
        screenshots.mkdir()
        target = canary / "marker.txt"
        relative_target = os.path.relpath(target, screenshots)
        (screenshots / "probe.txt").symlink_to(relative_target)
        member = "screenshots/probe.txt"
        external = target
    elif scenario == "setgid-target":
        # Matches the relevant POSIX inheritance conditions from the
        # historical ownership signature: a setgid GID-1001 parent.
        os.chmod(canary, 0o2755)
        (dest / "screenshots").symlink_to("../canary")
        member = "screenshots/probe.txt"
        external = canary / "probe.txt"
    else:
        raise ValueError(scenario)

    archive = base / "probe.zip"
    info = zipfile.ZipInfo(member)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100644 << 16)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, PAYLOAD)
    return archive, dest, external


def root_extract(helper: Path, extractor: str, archive: Path, dest: Path, log: Path) -> int:
    cmd = [
        "sudo", "-n", sys.executable, str(helper),
        "--extractor", extractor,
        "--archive", str(archive),
        "--dest", str(dest),
    ]
    with log.open("w", encoding="utf-8") as output:
        proc = subprocess.run(cmd, stdout=output, stderr=subprocess.STDOUT, text=True)
    return proc.returncode


def run_case(root: Path, evidence: Path, helper: Path, extractor: str, scenario: str) -> dict:
    base = root / extractor / scenario
    base.mkdir(parents=True)
    archive, dest, external = build_case(base, scenario)
    before = external.read_bytes() if external.exists() else None
    helper_log = evidence / f"{extractor}-{scenario}-root.log"
    rc = root_extract(helper, extractor, archive, dest, helper_log)
    after = external.read_bytes() if external.exists() else None
    redirected = after == PAYLOAD and (scenario != "final-file" or before != after)

    st = external.stat() if external.exists() else None
    result = {
        "extractor": extractor,
        "scenario": scenario,
        "helper_exit": rc,
        "external_target": str(external),
        "external_target_exists": external.exists(),
        "external_target_sha256": sha256(after) if after is not None else None,
        "external_write_detected": redirected,
        "target_uid": st.st_uid if st else None,
        "target_gid": st.st_gid if st else None,
        "target_mode": oct(st.st_mode & 0o777) if st else None,
        "target_signature_matches_0_1001_0644": bool(
            st and st.st_uid == 0 and st.st_gid == 1001
            and (st.st_mode & 0o777) == 0o644
        ),
        "dest_symlink_preserved": (
            (dest / "screenshots").is_symlink()
            or (dest / "screenshots" / "cache").is_symlink()
        ),
        "root_log": str(helper_log),
    }
    print(
        f"RESULT extractor={extractor} scenario={scenario} "
        f"helper_exit={rc} OUTSIDE_WRITE={'YES' if redirected else 'NO'} "
        f"target_uid={result['target_uid']} target_gid={result['target_gid']} "
        f"target_mode={result['target_mode']} "
        f"historical_signature_match={result['target_signature_matches_0_1001_0644']}"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--root-helper", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    helper = Path(args.root_helper).resolve()
    root.mkdir(parents=True, exist_ok=True)
    evidence = root / "evidence"
    evidence.mkdir()
    print(f"ORCHESTRATOR_UID={os.geteuid()} GID={os.getegid()}")
    print(f"ROOT={root}")
    print(f"ROOT_HELPER={helper}")

    check = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True)
    if check.returncode != 0:
        print("FATAL: passwordless sudo unavailable", file=sys.stderr)
        print(check.stderr, file=sys.stderr)
        return 2

    results = []
    for extractor in ("unzip", "zipfile"):
        for scenario in (
            "direct-directory", "nested-directory", "symlink-chain",
            "final-file", "setgid-target"
        ):
            results.append(run_case(root, evidence, helper, extractor, scenario))

    summary = {
        "orchestrator_uid": os.geteuid(),
        "cases": results,
        "outside_write_count": sum(r["external_write_detected"] for r in results),
        "historical_signature_match_count": sum(
            r["target_signature_matches_0_1001_0644"] for r in results
        ),
        "all_root_helpers_succeeded": all(r["helper_exit"] == 0 for r in results),
    }
    (evidence / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "T15 ROOT-VERIFIED PRE-EXISTING SYMLINK MATRIX",
        f"ORCHESTRATOR_UID={summary['orchestrator_uid']}",
        f"ALL_ROOT_HELPERS_SUCCEEDED={summary['all_root_helpers_succeeded']}",
        f"OUTSIDE_WRITE_COUNT={summary['outside_write_count']}/{len(results)}",
        f"HISTORICAL_0_1001_0644_SIGNATURE_MATCHES={summary['historical_signature_match_count']}",
    ]
    lines += [
        f"{r['extractor']} / {r['scenario']}: "
        f"outside_write={'YES' if r['external_write_detected'] else 'NO'}, "
        f"helper_exit={r['helper_exit']}, uid={r['target_uid']}, "
        f"gid={r['target_gid']}, mode={r['target_mode']}, "
        f"historical_match={r['target_signature_matches_0_1001_0644']}"
        for r in results
    ]
    (evidence / "SUMMARY.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if summary["all_root_helpers_succeeded"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
