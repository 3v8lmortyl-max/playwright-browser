import os, subprocess, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 24
DURATION = 56.85
OUT = Path("output")
OUT.mkdir(exist_ok=True)
AUDIO = OUT / "narration.wav"
VIDEO = OUT / "hackwithmorty_ai_cyber_campaign.mp4"
ASSETS = Path("screenshots")
BG = (5, 7, 12)
WHITE = (245, 247, 250)
MUTED = (165, 176, 190)
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)

# Actual narration phrase landmarks. Text is used as a centered intertitle only
# when no captured source image is assigned to that beat.
BEATS = [
 (0.25,3.40,"Artificial intelligence is changing cybersecurity."),
 (3.75,8.68,"But what happens when the same technology is used against the institutions it was meant to protect?"),
 (8.99,13.99,"On October twenty-second, a cyber campaign targeting the European Commission was detected."),
 (14.33,19.65,"Nearly a month later, officials confirmed something significant: AI tools had been used."),
 (19.94,23.78,"Several internal applications were temporarily taken offline as a precaution."),
 (24.46,28.82,"Most have since been restored. The investigation, however, is still underway."),
 (29.36,31.84,"Authorities have not publicly identified the attackers."),
 (32.45,34.72,"Nor have they established the full extent of the incident."),
 (35.31,39.63,"The Commission says it has found no evidence of a data breach affecting its staff as a whole."),
 (40.29,45.08,"The same technology can help defenders discover weaknesses—or help attackers find them first."),
 (45.84,48.92,"The difference is increasingly about who controls the tools."),
 (49.30,52.55,"AI is becoming part of the cybersecurity battlefield."),
 (52.81,56.65,"And the race to defend our digital infrastructure has already begun.")
]

# Download approved narration from a short-lived URL supplied as a GitHub secret.
audio_url = os.environ.get("NARRATION_URL", "").strip()
if not audio_url:
    saved = Path("narration_url.txt")
    if saved.exists():
        audio_url = saved.read_text(encoding="utf-8").strip()
if not audio_url:
    raise SystemExit("Missing NARRATION_URL secret.")
urllib.request.urlretrieve(audio_url, AUDIO)
if not AUDIO.exists() or AUDIO.stat().st_size < 10000:
    raise SystemExit("Narration download is empty or incomplete.")

images = sorted(ASSETS.glob("*.png"))
if not images:
    raise SystemExit("No source screenshots found in screenshots/.")
# Spread every captured image across the story beats; remaining beats become
# clean centered intertitles. No decorative cards, network graphics or overlays.
image_for_beat = {}
if len(images) >= len(BEATS):
    for i in range(len(BEATS)):
        image_for_beat[i] = images[round(i * (len(images)-1) / max(1, len(BEATS)-1))]
else:
    for i, path in enumerate(images):
        beat_index = round(i * (len(BEATS)-1) / max(1, len(images)-1))
        image_for_beat[beat_index] = path

def cover(path):
    src = Image.open(path).convert("RGB")
    scale = max(W/src.width, H/src.height)
    src = src.resize((round(src.width*scale), round(src.height*scale)), Image.Resampling.LANCZOS)
    left = (src.width-W)//2
    top = (src.height-H)//2
    return src.crop((left, top, left+W, top+H))

def title_card(text):
    im = Image.new("RGB", (W,H), BG)
    d = ImageDraw.Draw(im)
    f = font(64, True)
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = (line+" "+word).strip()
        if d.textbbox((0,0),trial,font=f)[2] <= 900:
            line = trial
        else:
            if line: lines.append(line)
            line = word
    if line: lines.append(line)
    heights = [d.textbbox((0,0),x,font=f)[3] for x in lines]
    y = (H-sum(heights)-28*(len(lines)-1))//2
    for line, height in zip(lines,heights):
        box = d.textbbox((0,0),line,font=f)
        d.text(((W-(box[2]-box[0]))//2,y),line,font=f,fill=WHITE)
        y += height+28
    return im

# Build a still-image documentary timeline with hard cuts and no motion effects.
frames = []
for i, (start,end,text) in enumerate(BEATS):
    still = cover(image_for_beat[i]) if i in image_for_beat else title_card(text)
    frames.append((start,end,still))

cmd = [
 "ffmpeg","-y","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","-",
 "-i",str(AUDIO),"-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","veryfast",
 "-crf","18","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k","-ar","48000",
 "-t",str(DURATION),"-movflags","+faststart",str(VIDEO)
]
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
try:
    for n in range(round(DURATION*FPS)):
        t = n/FPS
        chosen = frames[0][2]
        for start,end,still in frames:
            if start <= t < end:
                chosen = still
                break
        proc.stdin.write(chosen.tobytes())
    proc.stdin.close()
    code = proc.wait()
    if code:
        raise SystemExit(f"ffmpeg exited with {code}")
finally:
    if proc.poll() is None:
        proc.kill()

def stamp(sec):
    ms=round(sec*1000); h=ms//3600000; ms%=3600000; m=ms//60000; ms%=60000; s=ms//1000; ms%=1000
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

with open(OUT/"captions.srt","w",encoding="utf-8") as f:
    for i,(a,b,text) in enumerate(BEATS,1):
        f.write(f"{i}\n{stamp(a)} --> {stamp(b)}\n{text}\n\n")
print(f"VIDEO={VIDEO}")
print(f"SIZE={VIDEO.stat().st_size}")
print(f"RESOLUTION={W}x{H}")
print(f"IMAGES_USED={len(image_for_beat)}")
print(f"DURATION={DURATION}")
