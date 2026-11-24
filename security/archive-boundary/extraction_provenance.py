#!/usr/bin/env python3
"""T10 — extraction provenance reconstruction (unprivileged orchestrator).

Investigates which sequences of operations can produce the historical file
    /mnt/data/gh_demo/screenshots/01_github.com_01.png
with file 0:1001 mode 0644 and parent /mnt/data/gh_demo 0:1001 mode 2755,
extracted from a ZIP whose PNG entry has mode 0100644 and no UID/GID extra fields.

Only synthetic files and disposable temp directories are used. Nothing here
accesses, or claims knowledge of, ChatGPT's (or any platform's) filesystem or
processes; the historical values are treated purely as reference data.

Flow: build synthetic ZIPs as the unprivileged user -> run every scenario via the
narrowly scoped sudo runner (strace per step, ownership snapshot per step) ->
collect evidence -> write a chronological provenance report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WRAPPER = str(HERE / "extraction_provenance_wrapper.sh")

HISTORICAL = {
    "path": "/mnt/data/gh_demo/screenshots/01_github.com_01.png",
    "file_uid_gid": "0:1001",
    "file_mode": "0644",
    "parent": "/mnt/data/gh_demo",
    "parent_uid_gid": "0:1001",
    "parent_mode": "2755",
    "png_sha256": "dd0ff1aa6371838a1bd9d941b4b43a1596a01974c202a6967416f20916f61f51",
    "zip_sha256": "cafc11da650cb8d2be6564eadef1830e8f9b5cfd12541d24b85dd038676b2f65",
    "zip_png_entry_mode": "0100644",
    "zip_ownership_extra_fields": "none",
}

ENTRY = "screenshots/01_github.com_01.png"
ENTRY_TIME = (2026, 11, 23, 12, 0, 0)  # distinctive, so mtime restoration is visible
PNG_STANDIN = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)
OWNERSHIP_EXTRA_IDS = {0x000D: "PKWARE Unix (uid/gid)", 0x5855: "Info-ZIP Unix v1 (UX)",
                       0x7855: "Info-ZIP Unix v2 (Ux)", 0x7875: "Info-ZIP Unix v3 (ux)"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _zinfo(name, mode, is_dir=False):
    zi = zipfile.ZipInfo(name, date_time=ENTRY_TIME)
    zi.create_system = 3
    zi.external_attr = (mode << 16) | (0x10 if is_dir else 0)
    return zi


def build_origin(origin: Path) -> dict:
    origin.mkdir(mode=0o755)
    (origin / "standin.png").write_bytes(PNG_STANDIN)
    with zipfile.ZipFile(origin / "payload.zip", "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(_zinfo(ENTRY, 0o100644), PNG_STANDIN)
    with zipfile.ZipFile(origin / "payload_direntry.zip", "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(_zinfo("screenshots/", 0o040755, is_dir=True), b"")
        zf.writestr(_zinfo(ENTRY, 0o100644), PNG_STANDIN)
    for p in origin.iterdir():
        os.chmod(p, 0o644)
    return {p.name: inspect_zip(p) for p in sorted(origin.glob("*.zip"))} | {
        "standin.png": {"sha256": sha256(origin / "standin.png"), "size": len(PNG_STANDIN)}}


def _extra_ids(raw: bytes) -> list[str]:
    ids, i = [], 0
    while i + 4 <= len(raw):
        hid, ln = struct.unpack("<HH", raw[i:i + 4])
        ids.append(f"0x{hid:04x}")
        i += 4 + ln
    return ids


def inspect_zip(path: Path) -> dict:
    entries = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            ids = _extra_ids(info.extra)
            entries.append({
                "name": info.filename, "mode_octal": format(info.external_attr >> 16, "07o"),
                "create_system": info.create_system, "date_time": list(info.date_time),
                "extra_field_ids": ids,
                "ownership_extra_fields": [OWNERSHIP_EXTRA_IDS[int(x, 16)] for x in ids
                                           if int(x, 16) in OWNERSHIP_EXTRA_IDS],
            })
    return {"sha256": sha256(path), "size": path.stat().st_size, "entries": entries}


# --------------------------------------------------------------------------- #
def short(meta):
    return f"{meta['uid']}:{meta['gid']} {meta['perm_octal']}" if meta else "absent"


def build_report(results: dict, origin_meta: dict) -> dict:
    scen = results["scenarios"]
    reproduced = [s for s in scen if s["evaluation"]["full_match"]]
    ruled = [s for s in scen if not s["evaluation"]["full_match"]]

    def entry(s):
        ev = s["evaluation"]
        png = ev["png"] or {}
        return {
            "id": s["id"], "question": s["question"], "title": s["title"],
            "mechanism": s["mechanism"], "assumptions": s["assumptions"],
            "explicit_ownership_ops": s["explicit_ownership_ops"],
            "png": short(ev["png"]), "gh_demo": short(ev["gh_demo_dir"]),
            "screenshots": short(ev["screenshots_dir"]), "mnt_data": short(ev["mnt_data_dir"]),
            "png_mtime_utc": png.get("mtime_utc"),
            "png_mtime_restored_from_archive": bool(png.get("mtime_utc", "").startswith("2026-11-23T12:00:00")),
            "file_match": ev["file_match"], "parent_match": ev["parent_match"],
        }

    def why_ruled(s):
        ev = s["evaluation"]
        f, g = ev["png"], ev["gh_demo_dir"]
        r = []
        if not f:
            r.append("PNG not created")
        else:
            if f["uid"] != 0:
                r.append(f"file UID {f['uid']} (need 0)")
            if f["gid"] != 1001:
                r.append(f"file GID {f['gid']} (need 1001)")
            if f["perm_octal"] != "0644":
                r.append(f"file mode {f['perm_octal']} (need 0644)")
        if not g:
            r.append("gh_demo absent")
        else:
            if g["uid"] != 0 or g["gid"] != 1001:
                r.append(f"gh_demo {g['uid']}:{g['gid']} (need 0:1001)")
            if g["perm_octal"] != "2755":
                r.append(f"gh_demo mode {g['perm_octal']} (need 2755)")
        return r

    parsimonious = sorted((s for s in reproduced if not s["explicit_ownership_ops"]),
                          key=lambda s: (len(s["assumptions"]), s["id"]))
    zip_has_owner_fields = any(e["ownership_extra_fields"]
                               for z in origin_meta.values() if "entries" in z for e in z["entries"])

    unknowns = [
        "Which (if any) of the reproduced mechanisms actually ran: extractor binary/library, argv, "
        "and process credentials (execve/credential audit telemetry).",
        "Whether /mnt/data/gh_demo obtained group 1001 + setgid by inheritance from a setgid "
        "/mnt/data or by an explicit chgrp/chmod (metadata of /mnt/data and chown/chmod audit records).",
        "Whether any chown, chmod, rename, or replacement touched the PNG or its directories after "
        "creation (inode/ctime history, fanotify/audit logs).",
        "Identity mapping: whether UID 0 / GID 1001 inside the environment map to other IDs on the "
        "host (user namespaces, idmapped mounts) — platform configuration.",
        "Filesystem semantics of /mnt/data: mount options such as grpid/bsdgroups (group inheritance "
        "without setgid) or FUSE/9p/virtiofs/overlay ownership translation (mount table).",
        "Umask at extraction time; historical mtime/ctime/inode of the PNG, screenshots/ and gh_demo "
        "(mtime would distinguish unzip, which restores the entry time, from zipfile/cp).",
        "Metadata of the intermediate screenshots/ directory, which the historical evidence omits.",
        "Whether the extracted bytes came from the ZIP with SHA-256 cafc11… and match PNG SHA-256 "
        "dd0ff1… (synthetic files cannot verify historical content).",
        "How the ZIP was obtained (download tool, identity, location) — not inferable from the "
        "PNG's ownership, because ZIP ownership never propagates to extracted files.",
    ]

    evidence_ruled = [
        {"mechanism": "Ownership transported by the archive (ZIP uid/gid extra fields restored by "
                      "e.g. `unzip -X`)",
         "basis": "historical evidence: the original ZIP has no UID/GID ownership extra fields"},
    ]

    ok = {s["id"] for s in reproduced}
    ran = {s["id"] for s in scen}

    def held(cands):
        return ", ".join(i for i in cands if i in ok) or "none"

    def broke(cands):
        return ", ".join(i for i in cands if i in ran and i not in ok) or "none"

    chronology = []
    if parsimonious:
        chronology = [
            "T0 (prior, unknown actor) — a destination with group 1001 and the setgid bit exists: either "
            f"/mnt/data is setgid group 1001 (reproduced: {held(['S1a', 'S1c', 'S3a', 'S3c', 'S4b'])}) or "
            f"gh_demo itself was set 0:1001 2755 (reproduced: {held(['S4a'])}).",
            "T1 — the ZIP is materialized by any identity; its own ownership does not propagate "
            f"(root-owned ZIP: {held(['S1a', 'S1c'])}; unprivileged-owned ZIP: {held(['S3a', 'S3c'])}).",
            "T2 — a process running as UID 0 creates gh_demo (if absent), screenshots/ and the PNG via "
            "unzip or zipfile: directories inherit GID 1001 and setgid, the PNG inherits GID 1001, and "
            "mode 0644 comes from the entry's 0100644 (unzip) or umask 022 (zipfile).",
            "T3 — no later ownership-changing operation is required. Same-directory atomic replacement "
            f"keeps the signature ({held(['S6e'])}); these later operations break it: "
            f"{broke(['S6c', 'S6d', 'S5b', 'S5c', 'S6f', 'S6g', 'S6i'])}.",
        ]

    return {
        "experiment": "T10 extraction provenance reconstruction",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "historical_reference": HISTORICAL,
        "synthetic_inputs": origin_meta,
        "synthetic_zip_has_ownership_extra_fields": zip_has_owner_fields,
        "instrumentation": {"strace": results["strace"], "filter": results["strace_filter"],
                            "unprivileged_strace": results["unprivileged_strace"],
                            "unprivileged_identity": results["unprivileged_identity"]},
        "experimentally_reproduced": [entry(s) for s in reproduced],
        "consistent_with_historical_evidence": [
            entry(s) | {"note": ("requires an explicit ownership change after creation"
                                 if s["explicit_ownership_ops"] else
                                 "no ownership change after creation required")}
            for s in sorted(reproduced, key=lambda s: (s["explicit_ownership_ops"], len(s["assumptions"]), s["id"]))
        ],
        "ruled_out": [entry(s) | {"reasons": why_ruled(s)} for s in ruled] + evidence_ruled,
        "unknown_requires_platform_telemetry": unknowns,
        "candidate_chronology": chronology,
        "disclaimer": ("Synthetic reproduction of POSIX mechanics in disposable directories. No access to, "
                       "or claim about, any platform's internal filesystem or historical processes."),
    }


def write_markdown(path: Path, rep: dict, results: dict) -> None:
    h = rep["historical_reference"]
    L = ["# T10 — extraction provenance reconstruction", "",
         f"Generated (UTC): `{rep['generated_utc']}`", "",
         "## Historical reference (input data only)", "",
         f"- File `{h['path']}`: **{h['file_uid_gid']} mode {h['file_mode']}**, PNG SHA-256 `{h['png_sha256']}`",
         f"- Parent `{h['parent']}`: **{h['parent_uid_gid']} mode {h['parent_mode']}**",
         f"- ZIP SHA-256 `{h['zip_sha256']}`, PNG entry mode `{h['zip_png_entry_mode']}`, "
         f"ownership extra fields: {h['zip_ownership_extra_fields']}", "",
         f"Instrumentation: strace `{rep['instrumentation']['filter']}` per step; unprivileged identity "
         f"{rep['instrumentation']['unprivileged_identity']}; unprivileged steps traced: "
         f"{rep['instrumentation']['unprivileged_strace']}. Synthetic ZIP ownership extra fields: "
         f"{rep['synthetic_zip_has_ownership_extra_fields']}.", ""]

    L += ["## 1. Experimentally reproduced mechanisms", "",
          "| ID | Q | Mechanism | PNG | gh_demo | screenshots/ | PNG mtime from archive |",
          "|----|---|-----------|-----|---------|--------------|------------------------|"]
    for e in rep["experimentally_reproduced"]:
        L.append(f"| {e['id']} | {e['question']} | {e['mechanism']} | {e['png']} | {e['gh_demo']} | "
                 f"{e['screenshots']} | {e['png_mtime_restored_from_archive']} |")
    L += ["", "## 2. Mechanisms consistent with the historical evidence", ""]
    for e in rep["consistent_with_historical_evidence"]:
        a = "; ".join(e["assumptions"]) or "none"
        L.append(f"- **{e['id']}** — {e['title']}. Assumptions: {a}. {e['note']}.")
    L += ["", "## 3. Mechanisms ruled out", "",
          "| ID | Q | Mechanism | PNG | gh_demo | Why ruled out |", "|----|---|-----------|-----|---------|---------------|"]
    for e in rep["ruled_out"]:
        if "id" in e:
            L.append(f"| {e['id']} | {e['question']} | {e['mechanism']} | {e['png']} | {e['gh_demo']} | "
                     f"{'; '.join(e['reasons'])} |")
        else:
            L.append(f"| — | — | {e['mechanism']} | — | — | {e['basis']} |")
    L += ["", "## 4. Unknown operations requiring platform-side telemetry", ""]
    L += [f"- {u}" for u in rep["unknown_requires_platform_telemetry"]]
    if rep["candidate_chronology"]:
        L += ["", "## Candidate chronology (most parsimonious, experimentally reproduced)", ""]
        L += [f"{i}. {c}" for i, c in enumerate(rep["candidate_chronology"], 1)]
    L += ["", "## Chronological step log with ownership transitions", ""]
    for s in results["scenarios"]:
        ev = s["evaluation"]
        L += [f"### {s['id']} — {s['title']} → {'REPRODUCED' if ev['full_match'] else 'ruled out'}", "",
              "| # | Time (UTC) | Actor | Command | rc | Ownership transitions | Trace |",
              "|---|-----------|-------|---------|----|-----------------------|-------|"]
        for st in s["steps"]:
            tr = []
            for t in st["transitions"]:
                if t["event"] == "created":
                    tr.append(f"+{t['path']} {t['state']}")
                elif t["event"] == "changed":
                    d = ", ".join(f"{k} {v[0]}→{v[1]}" for k, v in t["delta"].items() if k != "mtime_utc")
                    if d:
                        tr.append(f"~{t['path']} {d}")
                else:
                    tr.append(f"-{t['path']}")
            cmd = " ".join(st["argv"])
            cmd = (cmd[:90] + "…") if len(cmd) > 90 else cmd
            L.append(f"| {st['index']} | {st['start_utc'][11:23]} | {st['who']} ({st['uid']}) umask {st['umask']} | "
                     f"`{cmd.replace('|', '/')}` | {st['returncode']} | {'<br>'.join(tr) or '—'} | "
                     f"{st['trace_file'] or st['trace_status']} |")
        L.append("")
    L += [f"> {rep['disclaimer']}", ""]
    path.write_text("\n".join(L), encoding="utf-8")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="T10 extraction provenance reconstruction")
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="/tmp")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--allow-root", action="store_true")
    args = ap.parse_args()

    if os.geteuid() == 0 and not args.allow_root:
        print("refusing to orchestrate as root (use --allow-root)", file=sys.stderr)
        return 2

    base = Path(args.base).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    lab_root = Path(tempfile.mkdtemp(prefix="archbound-t10-", dir=base))
    os.chmod(lab_root, 0o711)
    canary = lab_root / "_CANARY_DO_NOT_TOUCH"
    canary.write_text("confinement canary\n")
    canary_hash = sha256(canary)

    origin_meta = build_origin(lab_root / "origin")
    print(f"LAB_ROOT: {lab_root}")

    cmd = ["sudo", "-n", WRAPPER, str(base), "--lab-root", str(lab_root)]
    if args.only:
        cmd += ["--only", *args.only]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    (out / "runner_stdout.txt").write_text(proc.stdout)
    (out / "runner_stderr.txt").write_text(proc.stderr)
    print(proc.stdout, end="")
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)

    results_path = lab_root / "results.json"
    if not results_path.exists():
        print("scenario runner produced no results", file=sys.stderr)
        return 4
    results = json.loads(results_path.read_text())

    # Evidence: synthetic inputs, every scenario's step log + traces + final snapshot.
    (out / "origin").mkdir(exist_ok=True)
    for p in (lab_root / "origin").iterdir():
        shutil.copyfile(p, out / "origin" / p.name)
    for s in results["scenarios"]:
        d = out / "scenarios" / s["id"]
        (d / "traces").mkdir(parents=True, exist_ok=True)
        for t in (lab_root / s["id"] / "traces").glob("*.strace"):
            shutil.copyfile(t, d / "traces" / t.name)
        (d / "scenario.json").write_text(json.dumps(s, indent=2) + "\n")
    shutil.copyfile(results_path, out / "results.json")

    # Confinement guard.
    expected = {canary.name, "origin", "results.json"} | {s["id"] for s in results["scenarios"]}
    breaches = [e for e in os.listdir(lab_root) if e not in expected]
    canary_ok = canary.exists() and sha256(canary) == canary_hash

    report = build_report(results, origin_meta)
    report["confinement"] = {"lab_root": str(lab_root), "canary_ok": canary_ok,
                             "unexpected_lab_root_entries": breaches}
    (out / "provenance_report.json").write_text(json.dumps(report, indent=2) + "\n")
    write_markdown(out / "provenance_report.md", report, results)

    print(f"\nreproduced: {[e['id'] for e in report['experimentally_reproduced']]}")
    print(f"ruled out:  {[e.get('id', 'archive-ownership') for e in report['ruled_out']]}")
    print(f"canary_ok={canary_ok} breaches={breaches}")

    subprocess.run(["sudo", "-n", WRAPPER, str(base), "--lab-root", str(lab_root), "--cleanup"],
                   capture_output=True, text=True, timeout=120)
    return 3 if (breaches or not canary_ok) else 0


if __name__ == "__main__":
    sys.exit(main())
