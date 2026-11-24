#!/usr/bin/env python3
"""T12 read-only collector for EXISTING historical evidence in GitHub Actions artifacts.

Generates no experiments. It reads evidence that already exists:

1. The original artifact 14799922102: every central-directory entry (name, DOS
   timestamp, sizes, CRC, Unix mode) and the PNG's record in the original
   `capture_manifest.json`, which the capture script wrote on the original runner.
2. Every other retained, non-expired artifact in the repository that contains
   `screenshots/01_github.com_01.png`: that member's SHA-256 and DOS time. This
   tests whether artifact 14799922102 is the only retained source of the
   historical bytes.

Artifacts produced by the T1-T11 investigation suites are listed but not scanned,
because they are experimental and not historical. Archives are downloaded into a
temporary directory and only parsed with zipfile; nothing is extracted to disk
or executed. Output: one JSON evidence file and a printed summary.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "3v8lmortyl-max/playwright-browser"
ORIGINAL_ID = 14799922102
MEMBER = "screenshots/01_github.com_01.png"
HIST_SHA = "dd0ff1aa6371838a1bd9d941b4b43a1596a01974c202a6967416f20916f61f51"
ORIG_ZIP_SHA = "cafc11da650cb8d2be6564eadef1830e8f9b5cfd12541d24b85dd038676b2f65"
EXPERIMENTAL_PREFIXES = ("archive-boundary", "provenance-test-")


def api(url, token, raw=False):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28",
                                               "User-Agent": "t12-existing-evidence"})
    req.add_unredirected_header("Authorization", f"Bearer {token}")  # never sent to blob storage
    r = urllib.request.urlopen(req, timeout=180)
    return r if raw else json.loads(r.read())


def list_artifacts(base, token):
    out, page = [], 1
    while True:
        d = api(f"{base}/repos/{REPO}/actions/artifacts?per_page=100&page={page}", token)
        out += d.get("artifacts", [])
        if len(out) >= d.get("total_count", 0) or not d.get("artifacts"):
            return out
        page += 1


def download(base, token, aid, dest):
    h = hashlib.sha256()
    with api(f"{base}/repos/{REPO}/actions/artifacts/{aid}/zip", token, raw=True) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    return h.hexdigest()


def entry_meta(i: zipfile.ZipInfo) -> dict:
    return {"name": i.filename, "dos_time_naive": "%04d-%02d-%02dT%02d:%02d:%02d" % i.date_time,
            "size": i.file_size, "compressed": i.compress_size, "crc32": f"{i.CRC:08x}",
            "unix_mode": format(i.external_attr >> 16, "07o"), "create_system": i.create_system,
            "extra_len": len(i.extra)}


def main() -> int:
    token = os.environ.pop("GITHUB_TOKEN", None)
    if not token:
        print("GITHUB_TOKEN missing", file=sys.stderr)
        return 2
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "t12-evidence")
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"collected_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "repository": REPO, "member": MEMBER, "historical_sha256": HIST_SHA,
              "artifacts_listed": [], "artifacts_scanned": [], "original_artifact": None, "errors": []}

    arts = list_artifacts(base, token)
    for a in arts:
        result["artifacts_listed"].append({
            "id": a["id"], "name": a["name"], "size": a["size_in_bytes"], "expired": a["expired"],
            "created_at": a["created_at"], "digest": a.get("digest"),
            "workflow_run_id": (a.get("workflow_run") or {}).get("id"),
            "head_branch": (a.get("workflow_run") or {}).get("head_branch"),
            "class": ("experimental (T-suite / test)" if a["name"].startswith(EXPERIMENTAL_PREFIXES)
                      else "candidate historical")})

    with tempfile.TemporaryDirectory(prefix="t12-") as tmp:
        for a in arts:
            if a["expired"] or a["name"].startswith(EXPERIMENTAL_PREFIXES):
                continue
            zp = Path(tmp) / f"{a['id']}.zip"
            rec = {"id": a["id"], "name": a["name"], "created_at": a["created_at"],
                   "workflow_run_id": (a.get("workflow_run") or {}).get("id")}
            try:
                rec["zip_sha256"] = download(base, token, a["id"], zp)
                with zipfile.ZipFile(zp) as zf:
                    names = zf.namelist()
                    rec["entries"] = len(names)
                    if MEMBER in names:
                        info = zf.getinfo(MEMBER)
                        data = zf.read(info)
                        rec["member"] = entry_meta(info) | {"sha256": hashlib.sha256(data).hexdigest()}
                        rec["member_matches_historical"] = rec["member"]["sha256"] == HIST_SHA
                    else:
                        rec["member"] = None
                        rec["member_matches_historical"] = False
                    manifest = None
                    if "capture_manifest.json" in names:
                        manifest = json.loads(zf.read("capture_manifest.json"))
                        rec["capture_manifest_captured_at"] = manifest.get("captured_at")
                        rec["capture_manifest_png_record"] = next(
                            (x for x in manifest.get("assets", []) if x.get("file_name") == Path(MEMBER).name), None)
                    if a["id"] == ORIGINAL_ID:
                        result["original_artifact"] = {
                            "id": a["id"], "zip_sha256": rec["zip_sha256"],
                            "zip_sha256_matches_expected": rec["zip_sha256"] == ORIG_ZIP_SHA,
                            "central_directory": [entry_meta(i) for i in zf.infolist()],
                            "capture_manifest": ({k: v for k, v in manifest.items() if k != "assets"}
                                                 | {"assets": manifest.get("assets")}) if manifest else None}
            except (urllib.error.HTTPError, urllib.error.URLError, zipfile.BadZipFile, OSError, ValueError) as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"
                result["errors"].append(rec["error"])
            finally:
                zp.unlink(missing_ok=True)
            result["artifacts_scanned"].append(rec)

    (out_dir / "t12_existing_evidence.json").write_text(json.dumps(result, indent=2) + "\n")

    print(f"artifacts listed={len(arts)} scanned={len(result['artifacts_scanned'])} errors={len(result['errors'])}")
    for a in result["artifacts_listed"]:
        print(f"  LISTED id={a['id']} name={a['name']} created={a['created_at']} expired={a['expired']} "
              f"run={a['workflow_run_id']} class={a['class']}")
    print("== scanned artifacts: member screenshots/01_github.com_01.png ==")
    for r in result["artifacts_scanned"]:
        m = r.get("member") or {}
        print(f"  id={r['id']} run={r['workflow_run_id']} created={r['created_at']} zip_sha={r.get('zip_sha256', '?')[:16]} "
              f"member_sha={m.get('sha256', 'absent')[:16]} dos={m.get('dos_time_naive')} "
              f"MATCHES_HISTORICAL={r.get('member_matches_historical')} manifest_captured_at={r.get('capture_manifest_captured_at')} "
              f"error={r.get('error')}")
    o = result["original_artifact"]
    if o:
        print(f"== original artifact {o['id']} zip_sha_ok={o['zip_sha256_matches_expected']} ==")
        for e in o["central_directory"]:
            print(f"  CD {e['dos_time_naive']} size={e['size']:>9} mode={e['unix_mode']} extra={e['extra_len']} {e['name']}")
        cm = o["capture_manifest"] or {}
        print(f"  capture_manifest captured_at={cm.get('captured_at')} keys={sorted(k for k in cm if k != 'assets')}")
        for asset in cm.get("assets") or []:
            if asset.get("file_name") == Path(MEMBER).name:
                print(f"  manifest PNG record: {json.dumps(asset, ensure_ascii=False)[:900]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
