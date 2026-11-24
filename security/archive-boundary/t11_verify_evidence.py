#!/usr/bin/env python3
"""Verify a downloaded T11 evidence artifact and print its key findings.

Run in a separate job after `actions/download-artifact` has fetched
`archive-boundary-t11-evidence-<run_id>`. It checks that the evidence is complete
and internally consistent, that it was produced from the ORIGINAL artifact, and
that every successful scenario output carries the verified PNG SHA-256. It prints
the ZIP metadata and the per-operation timestamp behaviour so they can be read
straight from the job log. It exits non-zero when a mandatory check fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

EXPECTED_ZIP = "cafc11da650cb8d2be6564eadef1830e8f9b5cfd12541d24b85dd038676b2f65"
EXPECTED_PNG = "dd0ff1aa6371838a1bd9d941b4b43a1596a01974c202a6967416f20916f61f51"
REQUIRED = ["provenance_report.md", "provenance_report.json", "original_zip_manifest.json",
            "original_png_verification.json", "artifact_acquisition.json", "results.json",
            "historical_metadata_search.json", "filesystem_info.json", "T11_README.md"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("evidence_dir")
    ap.add_argument("--allow-self-test", action="store_true")
    a = ap.parse_args()
    d = Path(a.evidence_dir)
    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    files = [p for p in d.rglob("*") if p.is_file()]
    print(f"evidence files: {len(files)}")
    for r in REQUIRED:
        check((d / r).is_file(), f"required file present: {r}")
    if fails:
        return 1
    print("\n== top-level file SHA-256 ==")
    for r in REQUIRED:
        print(f"  {sha(d / r)}  {r}")

    rep = json.loads((d / "provenance_report.json").read_text())
    acq = json.loads((d / "artifact_acquisition.json").read_text())
    ver = json.loads((d / "original_png_verification.json").read_text())
    man = json.loads((d / "original_zip_manifest.json").read_text())
    res = json.loads((d / "results.json").read_text())
    fs = json.loads((d / "filesystem_info.json").read_text())
    srch = json.loads((d / "historical_metadata_search.json").read_text())

    print("\n== original artifact ==")
    selftest = rep.get("mode", "").startswith("SELF_TEST")
    check(not selftest or a.allow_self_test, f"report mode: {rep.get('mode')}")
    if not selftest:
        check(acq.get("sha256") == EXPECTED_ZIP, f"archive SHA-256 {acq.get('sha256')} == expected")
        check(ver["checks"]["sha256"]["observed"] == EXPECTED_PNG, "member SHA-256 == expected dd0ff1…")
        md = acq.get("api_metadata", {})
        print(f"  API: name={md.get('name')} size={md.get('size_in_bytes')} digest={md.get('digest')} "
              f"expired={md.get('expired')} created={md.get('created_at')} run={md.get('workflow_run_id')}")
        print(f"  download: http={acq.get('http_status')} host={acq.get('final_host')} bytes={acq.get('bytes')} "
              f"seconds={acq.get('seconds')}")
    check(ver["all_pass"], "all member verification checks pass")
    for k, v in ver["checks"].items():
        print(f"  {k:28s} observed={v['observed']} expected={v['expected']} pass={v['pass']}")

    print("\n== ZIP metadata (member) ==")
    cd, lfh = man["central_directory"], man["local_file_header"]
    print(f"  archive bytes={man['archive_size']} entries={man['entry_count']} zip64={man['zip64_eocd'] is not None}")
    print(f"  version made by: {cd['version_made_by']}  version needed: {cd['version_needed']}")
    print(f"  flags: {cd['general_purpose_flags']}  method: {cd['compression_method']}")
    print(f"  CD DOS time: {cd['dos_modification']}")
    print(f"  LFH DOS time: {lfh['dos_modification']['naive_local']} (matches CD: {lfh['dos_matches_central_directory']})")
    print(f"  crc32={cd['crc32']} compressed={cd['compressed_size']} uncompressed={cd['uncompressed_size']}")
    print(f"  external attributes: {cd['external_attributes']}  internal: {cd['internal_attributes']}")
    print(f"  CD extra fields: {[(x['id'], x['name'], x['length']) for x in cd['extra_fields']] or 'none'}")
    print(f"  LFH extra fields: {[(x['id'], x['name'], x['length']) for x in lfh['extra_fields']] or 'none'}")
    print(f"  data descriptor: {lfh['data_descriptor']}")
    print(f"  LFH fields: crc={lfh['crc32_field']} csize={lfh['compressed_size_field']} "
          f"usize={lfh['uncompressed_size_field']} name_matches_cd={lfh['name_matches_central_directory']}")
    print(f"  extended timestamp fields: {man['extended_timestamp_fields'] or 'none'}")
    print(f"  ownership extra fields: {man['ownership_extra_fields'] or 'none'}")

    print("\n== scenario integrity ==")
    scen = res["scenarios"]
    check(len(scen) == len(list((d / "scenarios").iterdir())), f"{len(scen)} scenarios have evidence dirs")
    traces = list((d / "scenarios").rglob("*.strace"))
    steps = sum(len(s["steps"]) for s in scen)
    check(len(traces) == steps, f"{len(traces)} strace files for {steps} steps")
    good = [s for s in scen if all(st["returncode"] == 0 for st in s["steps"] if st["role"] == "op")]
    bad_sha = [s["id"] for s in good if not s["evaluation"]["content_sha256_ok"]]
    check(not bad_sha, f"every successful output ({len(good)}) has the verified PNG SHA-256; mismatches: {bad_sha}")
    check(not rep["unrelated_entries_extracted_in"], "no unrelated archive entries extracted")
    check(rep["confinement"]["canary_ok"] and not rep["confinement"]["unexpected_entries"], "confinement guard intact")
    print(f"  classification counts: {rep['classification_counts']}")

    print("\n== filesystem ==")
    mi = fs.get("mountinfo") or {}
    print(f"  lab dev={fs.get('dev_major_minor')} statfs={fs.get('statfs')} fstype={mi.get('fstype') if isinstance(mi, dict) else mi} "
          f"mount_id={mi.get('mount_id') if isinstance(mi, dict) else '?'} kernel={fs.get('kernel')}")
    print(f"  historical device decoded: {fs.get('historical_device_decoded')}")
    print(f"  historical timestamp search: {srch.get('conclusion')}")

    print("\n== per-operation timestamp / ownership behaviour ==")
    for o in rep["operations"]:
        print(f"  {o['operation']}: verdict={o['verdict']} reproduced={o['reproduced_in']}")
        print(f"      mtime={o['mtime_behaviour']} example mtime={o.get('example_mtime_utc')} "
              f"ctime={o.get('example_ctime_utc')} birth={o.get('example_birth')} subsec_ns={o.get('mtime_subsecond_ns')}")
        print(f"      ctime set by={o.get('ctime_set_by')} ctime>mtime={o.get('ctime_after_mtime')} "
              f"time syscalls={o['timestamp_syscalls']} chown syscalls={o['ownership_syscalls']}")
        if o.get("rename_preserved_inode") is not None:
            print(f"      rename: inode kept={o['rename_preserved_inode']} mtime kept={o['rename_preserved_mtime']} "
                  f"ctime changed={o['rename_changed_ctime']} temp owner={o.get('temp_owner_before_rename')} "
                  f"replaced pre-existing inode={o.get('replaced_pre_existing_inode')}")
        if o["ruled_out_reasons"]:
            print(f"      ruled-out reasons: {o['ruled_out_reasons'][:4]}")

    print("\n== report statements ==")
    for k in ("what_the_original_zip_proves", "what_historical_metadata_supports", "eliminated_mechanisms"):
        print(f"  [{k}]")
        for x in rep[k]:
            print(f"   - {x}")

    print(f"\nRESULT: {'FAIL' if fails else 'PASS'} ({len(fails)} failed checks)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
