# HackWithMorty Documentary Pipeline

A reproducible pipeline for vertical documentary videos. It captures source pages at mobile resolution, records provenance for every image, builds a narration-aligned edit decision list, renders a full-screen still-image timeline, and validates the exported MP4.

## Pipeline

1. Capture: capture_targets.txt is the canonical source list. screenshot.py dismisses common cookie overlays using reject/essential-only choices (never accept-all), captures mobile viewport screenshots at 1080x1920, and writes capture_manifest.json with source URL, title, scroll position, visible text, dimensions and SHA-256. The capture validator rejects screenshots still contaminated by cookie dialogs or containing too little visible text.
2. Storyboard: storyboard.py scores each capture against narration beats using extracted viewport text, page title and source URL. Duration-based quotas control the number of shots per beat; stable source-order tie-breaking keeps the plan deterministic. Every captured image is assigned exactly once. Beats without an image become centered intertitles.
3. Render: render_video.py reads the actual narration duration using ffprobe, preserves source-image aspect ratios (no destructive crop), uses hard cuts and creates an external SRT. It also writes a storyboard JSON, contact sheet and render report.
4. Validate: validate_pipeline.py verifies capture dimensions and hashes; after rendering it checks image coverage, MP4 streams, resolution and duration.

## Run locally

Install Python requirements with pip install -r requirements.txt. Install Chromium with python -m playwright install chromium. Install ffmpeg separately and ensure ffmpeg and ffprobe are on PATH.

Run screenshot.py, then validate_pipeline.py --stage capture. Run pytest -q. Set NARRATION_URL to the HTTPS narration URL and run render_video.py. Finally run validate_pipeline.py --stage render.

Set NARRATION_URL as a GitHub Actions repository secret to render in CI. Do not commit signed narration URLs.

## Editorial constraints

- 1080x1920 vertical output, 24 fps.
- Source evidence fills the frame where possible; mismatched aspect ratios are letterboxed, never center-cropped.
- No generic network animations, floating UI cards or decorative transitions.
- Hard cuts between source visuals.
- Intertitles are centered and used only for narration beats without a source image.
- Every captured screenshot must appear in the edit decision list and final timeline.

## Source scope

The capture stage records web-page screenshots. It does not claim to download or license third-party video footage. Original footage should be added as an explicitly authorized, provenance-tracked asset before the storyboard stage.
