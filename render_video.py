"""Render a vertical, evidence-led documentary from captured source images."""
from __future__ import annotations
import json, os, shutil, subprocess, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from storyboard import build_storyboard, load_capture_manifest

W, H, FPS = 1080, 1920, 24
OUT = Path("output")
STILLS = OUT / "stills"
VIDEO = OUT / "hackwithmorty_ai_cyber_campaign.mp4"
AUDIO = OUT / "narration.wav"
BG, WHITE = (5, 7, 12), (245, 247, 250)
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def run_json(command: list[str]) -> dict:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)

def probe_audio(path: Path) -> float:
    data = run_json(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    duration = float(data["format"]["duration"])
    if duration <= 0:
        raise RuntimeError("Narration has an invalid duration")
    return duration

def download_narration() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    local_audio = os.environ.get("NARRATION_FILE", "").strip()
    if local_audio:
        source = Path(local_audio)
        if not source.is_file():
            raise SystemExit(f"NARRATION_FILE does not exist: {source}")
        if source.resolve() != AUDIO.resolve():
            shutil.copy2(source, AUDIO)
        if AUDIO.stat().st_size < 10_000:
            raise SystemExit("Local narration file is empty or incomplete.")
        return

    url = os.environ.get("NARRATION_URL", "").strip()
    if not url:
        raise SystemExit("Missing NARRATION_URL repository secret.")
    if not url.startswith("https://"):
        raise SystemExit("NARRATION_URL must use HTTPS.")
    partial = AUDIO.with_suffix(".wav.part")
    request = urllib.request.Request(url, headers={"User-Agent": "HackWithMorty-Documentary/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as target:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
        if partial.stat().st_size < 10_000:
            raise RuntimeError("Narration download is empty or incomplete")
        partial.replace(AUDIO)
    finally:
        partial.unlink(missing_ok=True)

def make_title_card(text: str, output: Path) -> None:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT_BOLD, 62)
    lines, current = [], ""
    for word in text.split():
        candidate = (current + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= 900:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    heights, gap = [draw.textbbox((0, 0), line, font=font)[3] for line in lines], 28
    y = (H - sum(heights) - gap * max(0, len(lines) - 1)) // 2
    for line, height in zip(lines, heights):
        bounds = draw.textbbox((0, 0), line, font=font)
        draw.text(((W - (bounds[2] - bounds[0])) // 2, y), line, font=font, fill=WHITE)
        y += height + gap
    image.save(output, format="PNG", optimize=True)

def prepare_still(source: Path, output: Path) -> None:
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    # Preserve all source content: fit, never destructive center-crop.
    fitted = ImageOps.contain(image, (W, H), method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (W, H), BG)
    canvas.paste(fitted, ((W - fitted.width) // 2, (H - fitted.height) // 2))
    canvas.save(output, format="PNG", optimize=True)

def stamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"

def write_srt(captions: list[dict]) -> None:
    with (OUT / "captions.srt").open("w", encoding="utf-8") as handle:
        for item in captions:
            handle.write(f"{item['index']}\n{stamp(item['start'])} --> {stamp(item['end'])}\n{item['text']}\n\n")

def quote_concat(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")

def make_contact_sheet(stills: list[Path], labels: list[str]) -> None:
    thumb_w, thumb_h, columns = 180, 320, 5
    rows = (len(stills) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * thumb_w, rows * (thumb_h + 34)), (20, 23, 29))
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.truetype(FONT_REG, 15)
    for index, (path, label) in enumerate(zip(stills, labels)):
        with Image.open(path) as image:
            thumb = image.convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        x, y = (index % columns) * thumb_w, (index // columns) * (thumb_h + 34)
        sheet.paste(thumb, (x, y))
        draw.text((x + 5, y + thumb_h + 7), label[:24], font=label_font, fill=WHITE)
    sheet.save(OUT / "storyboard_contact_sheet.jpg", quality=88)

def main() -> None:
    download_narration()
    duration = probe_audio(AUDIO)
    manifest = load_capture_manifest()
    plan = build_storyboard(manifest, duration)
    OUT.mkdir(parents=True, exist_ok=True)
    if STILLS.exists():
        shutil.rmtree(STILLS)
    STILLS.mkdir(parents=True)

    still_paths, labels = [], []
    for index, shot in enumerate(plan["timeline"], start=1):
        still_path = STILLS / f"shot_{index:04}.png"
        if shot["kind"] == "image":
            source = Path(shot["path"])
            if not source.is_file():
                raise FileNotFoundError(f"Source image missing: {source}")
            prepare_still(source, still_path)
            labels.append(Path(shot["path"]).name)
        else:
            make_title_card(shot["text"], still_path)
            labels.append("INTERTITLE")
        shot["rendered_still"] = str(still_path)
        still_paths.append(still_path)

    (OUT / "storyboard.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    write_srt(plan["captions"])
    make_contact_sheet(still_paths, labels)

    concat_path = OUT / "timeline.ffconcat"
    with concat_path.open("w", encoding="utf-8") as handle:
        handle.write("ffconcat version 1.0\n")
        for shot, still_path in zip(plan["timeline"], still_paths):
            handle.write(f"file '{quote_concat(still_path)}'\nduration {max(0.05, shot['end'] - shot['start']):.6f}\n")
        handle.write(f"file '{quote_concat(still_paths[-1])}'\n")

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0", "-i", str(concat_path), "-i", str(AUDIO),
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", f"fps={FPS},setsar=1,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.6f}",
        "-movflags", "+faststart", str(VIDEO),
    ]
    subprocess.run(command, check=True)
    video_probe = run_json([
        "ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_type,width,height",
        "-of", "json", str(VIDEO),
    ])
    video_stream = next(s for s in video_probe["streams"] if s["codec_type"] == "video")
    report = {
        "video": str(VIDEO), "file_size_bytes": VIDEO.stat().st_size,
        "duration_seconds": float(video_probe["format"]["duration"]),
        "audio_duration_seconds": duration,
        "resolution": f"{video_stream['width']}x{video_stream['height']}",
        "fps": FPS, "captured_images": len(manifest["assets"]),
        "images_used": plan["assets_used"],
        "intertitles": sum(1 for item in plan["timeline"] if item["kind"] == "intertitle"),
        "timeline_shots": len(plan["timeline"]),
        "all_images_used": plan["assets_used"] == len(manifest["assets"]),
    }
    (OUT / "render_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
