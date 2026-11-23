"""Fail-fast checks for capture output and rendered video."""
from __future__ import annotations
import argparse, hashlib, json, subprocess
from pathlib import Path

MANIFEST = Path("capture_manifest.json")
VIDEO = Path("output/hackwithmorty_ai_cyber_campaign.mp4")
REPORT = Path("output/render_report.json")
STORYBOARD = Path("output/storyboard.json")
CAPTIONS = Path("output/captions.srt")

def probe(path: Path) -> dict:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_type,width,height", "-of", "json", str(path),
    ], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def validate_capture() -> dict:
    if not MANIFEST.is_file():
        raise SystemExit("capture_manifest.json is missing; run screenshot.py first.")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assets = manifest.get("assets", [])
    if not assets:
        raise SystemExit("Capture manifest contains zero assets.")
    errors, seen = [], set()
    if manifest.get("failures"):
        errors.extend(f"Source failed: {item.get('url')} ({item.get('error')})" for item in manifest["failures"])
    for asset in assets:
        path = Path(asset["path"])
        if not path.is_file():
            errors.append(f"Missing image: {path}")
            continue
        if path.name in seen:
            errors.append(f"Duplicate filename in manifest: {path.name}")
        seen.add(path.name)
        if (asset.get("width"), asset.get("height")) != (1080, 1920):
            errors.append(f"Unexpected dimensions for {path.name}: {asset.get('width')}x{asset.get('height')}")
        if asset.get("sha256") and sha256_file(path) != asset["sha256"]:
            errors.append(f"SHA-256 mismatch: {path.name}")
    if errors:
        raise SystemExit("Capture validation failed:\n- " + "\n- ".join(errors))
    return {"status": "passed", "assets": len(assets), "dimensions": "1080x1920"}

def validate_render() -> dict:
    validate_capture()
    for path in (VIDEO, REPORT, STORYBOARD, CAPTIONS):
        if not path.is_file():
            raise SystemExit(f"Render output missing: {path}")
    if VIDEO.stat().st_size < 100_000:
        raise SystemExit("Rendered MP4 is unexpectedly small.")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    plan = json.loads(STORYBOARD.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = [Path(asset["path"]).name for asset in manifest["assets"]]
    used = [Path(shot["path"]).name for shot in plan["timeline"] if shot["kind"] == "image"]
    if sorted(expected) != sorted(used) or len(used) != len(expected):
        raise SystemExit("Image coverage mismatch: every captured image must appear exactly once in the timeline.")
    if report.get("images_used") != len(expected) or not report.get("all_images_used"):
        raise SystemExit("Render report does not confirm complete image coverage.")
    metadata = probe(VIDEO)
    streams = metadata.get("streams", [])
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(videos) != 1 or not audios:
        raise SystemExit("MP4 must contain exactly one video stream and at least one audio stream.")
    if (videos[0].get("width"), videos[0].get("height")) != (1080, 1920):
        raise SystemExit(f"Unexpected dimensions: {videos[0].get('width')}x{videos[0].get('height')}")
    actual, expected_duration = float(metadata["format"]["duration"]), float(report["audio_duration_seconds"])
    if abs(actual - expected_duration) > 0.75:
        raise SystemExit(f"Duration mismatch: video={actual:.3f}s narration={expected_duration:.3f}s")
    return {"status": "passed", "video": str(VIDEO), "duration_seconds": round(actual, 3),
            "resolution": "1080x1920", "image_coverage": f"{len(used)}/{len(expected)}",
            "audio_stream": True, "captions": True}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("capture", "render"), required=True)
    args = parser.parse_args()
    result = validate_capture() if args.stage == "capture" else validate_render()
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
