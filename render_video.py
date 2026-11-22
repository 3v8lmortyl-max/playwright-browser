import os, math, subprocess, urllib.request
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 720, 1280, 24
DURATION = 56.85
TOTAL = round(DURATION * FPS)
OUT = Path("output")
OUT.mkdir(exist_ok=True)
AUDIO = Path("output/narration.wav")
VIDEO = OUT / "hackwithmorty_ai_cyber_campaign.mp4"
ASSET = Path("screenshots")
BG = (5, 7, 12)
WHITE = (238, 242, 247)
MUTED = (133, 148, 166)
RED = (255, 65, 75)
CYAN = (36, 210, 226)
GRID = (18, 27, 39)

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)

def rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

def fit_text(draw, text, max_width, start=54, bold=True):
    size = start
    while size > 20 and draw.textbbox((0,0), text, font=font(size,bold))[2] > max_width:
        size -= 2
    return font(size,bold)

def wrap(draw, text, fnt, max_width):
    words, lines, current = text.split(), [], ""
    for word in words:
        test = (current + " " + word).strip()
        if draw.textbbox((0,0), test, font=fnt)[2] <= max_width:
            current = test
        else:
            if current: lines.append(current)
            current = word
    if current: lines.append(current)
    return lines

def draw_centered(draw, text, y, fnt, fill=WHITE, max_width=620, spacing=12):
    lines = wrap(draw, text, fnt, max_width)
    for line in lines:
        box = draw.textbbox((0,0), line, font=fnt)
        draw.text(((W-(box[2]-box[0]))/2, y), line, font=fnt, fill=fill)
        y += box[3]-box[1]+spacing
    return y

def load_screenshot(pattern):
    files = list(ASSET.glob(pattern))
    if not files: return None
    return Image.open(files[0]).convert("RGB")

official = load_screenshot("01_audiovisual*.png")
euronext = load_screenshot("02_live.euronext*.png") or load_screenshot("02_*.png")

# Download the approved narration once; the URL is short-lived and is removed from the repo after this run.
audio_url = os.environ.get("NARRATION_URL", "").strip()
if not audio_url:
    p = Path("narration_url.txt")
    if p.exists(): audio_url = p.read_text().strip()
if not audio_url:
    raise SystemExit("Missing NARRATION_URL.")
urllib.request.urlretrieve(audio_url, AUDIO)
if not AUDIO.exists() or AUDIO.stat().st_size < 10000:
    raise SystemExit("Narration download is empty or incomplete.")

# Phrase cues use the actual HeyGen word timing landmarks, grouped into readable phrases.
CAPTIONS = [
 (0.25,3.40,"Artificial intelligence is changing cybersecurity."),
 (3.75,8.68,"What happens when it's used against the institutions it should protect?"),
 (8.99,13.99,"On October twenty-second, a cyber campaign targeting the European Commission was detected."),
 (14.33,19.65,"Officials confirmed that AI tools had been used."),
 (19.94,23.78,"Several internal applications were temporarily taken offline as a precaution."),
 (24.46,28.82,"Most have been restored. The investigation is still underway."),
 (29.36,34.72,"Authorities have not identified the attackers or the full extent of the incident."),
 (35.31,39.63,"The Commission found no evidence of a data breach affecting staff as a whole."),
 (40.29,45.08,"The same technology can help defenders discover weaknesses—or help attackers find them first."),
 (45.84,48.92,"The difference is increasingly about who controls the tools."),
 (49.30,56.65,"AI is part of the cybersecurity battlefield. The race to defend our digital infrastructure has begun.")
]

def background(t, scene):
    im = Image.new("RGB",(W,H),BG)
    px = im.load()
    # Restrained radial glow, red/cyan split by scene.
    cx, cy = (W*.72,H*.34) if scene in (1,4) else (W*.32,H*.42)
    accent = RED if scene in (0,2,3,5) else CYAN
    for y in range(H):
        for x in range(W):
            d = math.sqrt(((x-cx)/W)**2 + ((y-cy)/H)**2)
            glow = max(0, 1-d*2.1) * 0.12
            px[x,y] = tuple(min(255, int(BG[k] + accent[k]*glow)) for k in range(3))
    d = ImageDraw.Draw(im, "RGBA")
    offset = int(t*18)%48
    for x in range(-48,W+48,48):
        d.line((x+offset,0,x+offset,H), fill=(32,45,61,45), width=1)
    for y in range(-48,H+48,48):
        d.line((0,y+offset,W,y+offset), fill=(32,45,61,45), width=1)
    return im

def chain_mark(draw, cx, cy, scale=1.0, color=CYAN):
    r = int(27*scale)
    draw.ellipse((cx-r*1.45,cy-r,cx+r*.35,cy+r), outline=color, width=max(3,int(7*scale)))
    draw.ellipse((cx-r*.35,cy-r,cx+r*1.45,cy+r), outline=color, width=max(3,int(7*scale)))

def network(draw, t, scene):
    pts=[]
    for i in range(11):
        x = 65 + ((i*79 + int(t*24))%590)
        y = 250 + ((i*137 + scene*53)%660)
        pts.append((x,y))
    for i,(x,y) in enumerate(pts):
        for j,(xx,yy) in enumerate(pts):
            if j>i and (i+j+scene)%3==0:
                draw.line((x,y,xx,yy), fill=(CYAN[0],CYAN[1],CYAN[2],85), width=2)
    for i,(x,y) in enumerate(pts):
        r=7+int(3*(.5+.5*math.sin(t*3+i)))
        color=RED if (i+scene)%4==0 else CYAN
        draw.ellipse((x-r,y-r,x+r,y+r), fill=color+(190,))

def browser_panel(base, shot, t, label):
    if shot is None: return
    # crop and scale the actual browser capture into a readable editorial evidence panel
    target_w=650; target_h=470
    src=shot.copy()
    ratio=max(target_w/src.width,target_h/src.height)
    src=src.resize((int(src.width*ratio),int(src.height*ratio)),Image.Resampling.LANCZOS)
    left=(src.width-target_w)//2; top=(src.height-target_h)//2
    src=src.crop((left,top,left+target_w,top+target_h))
    x=35; y=300+int(8*math.sin(t*1.2))
    base.paste(src,(x,y))
    d=ImageDraw.Draw(base)
    d.rectangle((x,y,x+target_w,y+target_h),outline=(65,82,101),width=2)
    rounded(d,(x,y-44,x+target_w,y-4),12,(17,24,35))
    d.ellipse((x+16,y-30,x+28,y-18),fill=RED)
    d.ellipse((x+38,y-30,x+50,y-18),fill=(237,184,70))
    d.ellipse((x+60,y-30,x+72,y-18),fill=CYAN)
    d.text((x+95,y-35),label,font=font(18),fill=(190,203,217))

def scene_content(im,t,scene):
    d=ImageDraw.Draw(im,"RGBA")
    if scene==0:
        network(d,t,scene)
        draw_centered(d,"AI IS CHANGING",170,font(48,True),WHITE)
        draw_centered(d,"CYBERSECURITY",230,font(58,True),RED)
        chain_mark(d,360,790,1.2,CYAN)
        d.text((52,1030),"THE SAME TOOL. TWO SIDES.",font=font(25,True),fill=WHITE)
    elif scene==1:
        d.text((48,160),"DOCUMENTED INCIDENT",font=font(22,True),fill=CYAN)
        d.text((48,205),"BRUSSELS",font=font(60,True),fill=WHITE)
        d.text((48,278),"EUROPEAN COMMISSION",font=font(28,True),fill=WHITE)
        browser_panel(im,official,t,"EUROPEAN COMMISSION • OFFICIAL BRIEFING")
        d=ImageDraw.Draw(im,"RGBA")
        rounded(d,(42,840,678,960),16,(14,20,29,245),outline=RED,width=2)
        d.text((68,865),"22 OCT 2026",font=font(37,True),fill=RED)
        d.text((68,918),"INCIDENT DETECTED",font=font(24,True),fill=WHITE)
    elif scene==2:
        d.text((48,160),"WHAT WAS CONFIRMED",font=font(22,True),fill=CYAN)
        d.text((48,220),"AI-ASSISTED",font=font(60,True),fill=WHITE)
        d.text((48,290),"CYBER CAMPAIGN",font=font(52,True),fill=RED)
        network(d,t,scene)
        # application nodes visibly drop, then return
        for i in range(5):
            x=90+i*135; y=780
            active = ((t*1.4+i*.45)%4.5)>2.1
            c=CYAN if active else RED
            rounded(d,(x-42,y-42,x+42,y+42),14,(17,27,39),outline=c,width=4)
            d.ellipse((x-10,y-10,x+10,y+10),fill=c)
        d.text((70,900),"SEVERAL APPLICATIONS",font=font(27,True),fill=WHITE)
        d.text((70,944),"TEMPORARILY TAKEN OFFLINE",font=font(22,True),fill=RED)
        d.text((70,1005),"MOST HAVE SINCE BEEN RESTORED",font=font(20,True),fill=CYAN)
    elif scene==3:
        d.text((48,160),"WHAT IS NOT YET KNOWN",font=font(22,True),fill=CYAN)
        d.text((48,220),"INVESTIGATION",font=font(48,True),fill=WHITE)
        d.text((48,280),"STILL ONGOING",font=font(52,True),fill=RED)
        # unresolved question marks over a network
        network(d,t,scene)
        rounded(d,(52,880,668,1060),18,(12,18,27,240),outline=(255,65,75),width=2)
        d.text((82,910),"NO PUBLIC ATTRIBUTION",font=font(28,True),fill=WHITE)
        d.text((82,966),"NO CONFIRMED FULL SCOPE",font=font(24,True),fill=RED)
    elif scene==4:
        d.text((48,150),"THE DUAL-USE PROBLEM",font=font(22,True),fill=CYAN)
        # split comparison, aligned columns
        rounded(d,(42,260,348,810),20,(10,22,27),outline=CYAN,width=3)
        rounded(d,(372,260,678,810),20,(28,14,20),outline=RED,width=3)
        d.text((82,310),"DEFENCE",font=font(32,True),fill=CYAN)
        d.text((415,310),"ATTACK",font=font(32,True),fill=RED)
        for i in range(4):
            yy=420+i*75
            d.line((95,yy,285,yy),fill=(36,210,226,120),width=4)
            d.line((425,yy,615,yy),fill=(255,65,75,120),width=4)
            r=11+int(4*math.sin(t*3+i))
            d.ellipse((180-r,yy-r,180+r,yy+r),fill=CYAN)
            d.ellipse((510-r,yy-r,510+r,yy+r),fill=RED)
        draw_centered(d,"ONE CAPABILITY. DIFFERENT INTENT.",880,font(27,True),WHITE)
        draw_centered(d,"AI can help identify vulnerabilities.",935,font(22),MUTED)
    elif scene==5:
        network(d,t,scene)
        chain_mark(d,360,420,1.5,CYAN)
        draw_centered(d,"WHO CONTROLS",600,font(54,True),WHITE)
        draw_centered(d,"THE TOOLS?",670,font(64,True),RED)
        draw_centered(d,"THE DEFENCE RACE HAS BEGUN.",830,font(26,True),CYAN)
        d.text((48,1110),"HACKWITHMORTY",font=font(23,True),fill=WHITE)
        d.text((48,1150),"CYBERSECURITY • CURRENT AFFAIRS",font=font(16,True),fill=MUTED)

def active_caption(t):
    for a,b,s in CAPTIONS:
        if a <= t < b: return s
    return ""

def add_caption(im, text):
    if not text: return
    d=ImageDraw.Draw(im,"RGBA")
    f=font(27,True)
    lines=wrap(d,text,f,610)
    line_h=38
    total=len(lines)*line_h+30
    y=H-250
    rounded(d,(38,y,682,y+total),14,(0,0,0,205),outline=(255,255,255,35),width=1)
    yy=y+15
    for line in lines:
        bb=d.textbbox((0,0),line,font=f)
        d.text(((W-(bb[2]-bb[0]))/2,yy),line,font=f,fill=WHITE,stroke_width=1,stroke_fill=(0,0,0,200))
        yy+=line_h

# Six story beats aligned to actual narration word timestamps.
SCENES=[(0,8.9,0),(8.9,19.7,1),(19.7,28.9,2),(28.9,39.9,3),(39.9,48.95,4),(48.95,56.85,5)]
def scene_at(t):
    for a,b,s in SCENES:
        if a<=t<b: return a,b,s
    return SCENES[-1]

cmd=[
 "ffmpeg","-y","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","-",
 "-i",str(AUDIO),"-map","0:v:0","-map","1:a:0","-c:v","libx264","-preset","veryfast",
 "-crf","19","-pix_fmt","yuv420p","-c:a","aac","-b:a","192k","-ar","48000",
 "-t",str(DURATION),"-movflags","+faststart",str(VIDEO)
]
proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
try:
    for n in range(TOTAL):
        t=n/FPS
        a,b,s=scene_at(t)
        local=t-a
        im=background(t,s)
        scene_content(im,local,s)
        # source evidence is a real browser capture, not a fabricated article image
        if s==1 and official is not None:
            pass
        add_caption(im,active_caption(t))
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    code=proc.wait()
    if code: raise SystemExit(f"ffmpeg exited with {code}")
finally:
    if proc.poll() is None:
        proc.kill()

# Plain subtitle sidecar (same phrase cue times as burned-in captions).
def stamp(sec):
    ms=round(sec*1000); h=ms//3600000; ms%=3600000; m=ms//60000; ms%=60000; s=ms//1000; ms%=1000
    return f"{h:02}:{m:02}:{s:02},{ms:03}"
with open(OUT/"captions.srt","w",encoding="utf-8") as f:
    for i,(a,b,text) in enumerate(CAPTIONS,1):
        f.write(f"{i}\n{stamp(a)} --> {stamp(b)}\n{text}\n\n")
print(f"VIDEO={VIDEO}")
print(f"SIZE={VIDEO.stat().st_size}")
print(f"DURATION={DURATION}")
