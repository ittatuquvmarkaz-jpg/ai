"""Render a vertical reel from job.json: per-scene image + voice clip + captions, music bed, cover.

job.json:
{ "name": "batareya_02", "music": "<url|path>", "music_gain_db": -17,
  "cover": {"title1": "...", "title2": "...", "badge": "..."},
  "handle": "@ai.elektronika",
  "scenes": [ {"image": "<url|path>", "audio": "<url|path>", "label": "1-SIR", "text": "spoken text for captions"} ] }
"""
import json, math, os, subprocess, sys, urllib.request, re
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "..", "fonts")
ANTON = os.path.join(FONTS, "Anton-Regular.ttf")
INTERB = os.path.join(FONTS, "Inter-ExtraBold.otf")
YEL = (255, 214, 0); WHITE = (255, 255, 255); CYAN = (0, 229, 255); BLACK = (0, 0, 0)

def fetch(src, dst):
    if re.match(r"https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
            f.write(r.read())
    else:
        with open(src, "rb") as a, open(dst, "wb") as b: b.write(a.read())
    return dst

def duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return float(out.strip())

def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease(x): x = clamp(x); return 1 - (1 - x) ** 3
def back(x, s=2.2): x = clamp(x) - 1; return x * x * ((s + 1) * x + s) + 1

def drop_seam(img):
    """Some generated images contain a hard horizontal panel seam in the upper part; keep the clean part below it."""
    g = img.convert("L").resize((img.width // 4, img.height // 4))
    w, h = g.size; px = g.load()
    best, by = 0, None
    for y in range(int(h * 0.12), int(h * 0.5)):
        d = sum(abs(px[x, y] - px[x, y - 1]) for x in range(0, w, 2)) / (w / 2)
        nb = sum(abs(px[x, y - 3] - px[x, y - 4]) for x in range(0, w, 2)) / (w / 2) + 1
        score = d / nb
        if d > 18 and score > best: best, by = score, y
    if by and best > 4:
        cut = by * 4 + 6
        print(f"seam at {cut}px (score {best:.1f}) -> cropping", flush=True)
        return img.crop((0, cut, img.width, img.height))
    return img

def cover_fit(img):
    img = drop_seam(img)
    r = max(W / img.width, H / img.height)
    img = img.resize((math.ceil(img.width * r), math.ceil(img.height * r)), Image.LANCZOS)
    l, t = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((l, t, l + W, t + H))

def stroke_text(text, font, fill, stroke=10, shadow=True, maxw=980):
    # wrap
    words = text.split(); lines = []; cur = ""
    for w in words:
        t = (cur + " " + w).strip()
        if font.getlength(t) <= maxw or not cur: cur = t
        else: lines.append(cur); cur = w
    if cur: lines.append(cur)
    asc, desc = font.getmetrics(); lh = int((asc + desc) * 1.02)
    w = int(max(font.getlength(l) for l in lines)) + stroke * 2 + 40
    h = lh * len(lines) + stroke * 2 + 40
    s = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(s)
    for i, l in enumerate(lines):
        x = (w - font.getlength(l)) / 2; y = 20 + stroke + i * lh
        d.text((x, y), l, font=font, fill=fill + (255,), stroke_width=stroke, stroke_fill=BLACK + (255,))
    if shadow:
        sh = Image.new("RGBA", s.size, (0, 0, 0, 0)); sh.putalpha(s.getchannel("A").filter(ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.6)))
        out = Image.new("RGBA", (w + 12, h + 12), (0, 0, 0, 0)); out.alpha_composite(sh, (10, 12)); out.alpha_composite(s, (0, 0)); s = out
    return s

def put(im, spr, cx, cy, alpha=1.0, scale=1.0):
    if alpha <= 0.01: return
    if abs(scale - 1) > 0.004:
        spr = spr.resize((max(1, int(spr.width * scale)), max(1, int(spr.height * scale))), Image.LANCZOS)
    if alpha < 0.999:
        spr = spr.copy(); spr.putalpha(spr.getchannel("A").point(lambda v: int(v * alpha)))
    im.alpha_composite(spr, (int(cx - spr.width / 2), int(cy - spr.height / 2)))

def badge(text, font, bg, fg=BLACK, pad=(34, 10), angle=0):
    tw = font.getlength(text); asc, desc = font.getmetrics()
    w, h = int(tw + pad[0] * 2), int(asc + desc + pad[1] * 2)
    b = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(b)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=bg + (255,))
    d.text((pad[0], pad[1]), text, font=font, fill=fg + (255,))
    return b.rotate(angle, expand=True, resample=Image.BICUBIC) if angle else b

def chunks(text, n=2):
    words = re.sub(r"[—–]", " ", text).split()
    out = [" ".join(words[i:i + n]) for i in range(0, len(words), n)]
    return out

def main(job_path, out_dir):
    job = json.load(open(job_path)); name = job["name"]
    os.makedirs(out_dir, exist_ok=True); tmp = os.path.join(out_dir, "_tmp"); os.makedirs(tmp, exist_ok=True)
    f_cap = ImageFont.truetype(ANTON, 104); f_title1 = ImageFont.truetype(ANTON, 170); f_title2 = ImageFont.truetype(ANTON, 128)
    f_lab = ImageFont.truetype(ANTON, 100); f_badge = ImageFont.truetype(ANTON, 80); f_handle = ImageFont.truetype(INTERB, 44)
    LEAD, TAIL = 0.15, 0.35
    scenes = []; t0 = 0.0
    for i, sc in enumerate(job["scenes"]):
        img = cover_fit(Image.open(fetch(sc["image"], f"{tmp}/img{i}")).convert("RGB")).convert("RGBA")
        # extra-large copy for ken burns
        big = img.resize((int(W * 1.18), int(H * 1.18)), Image.LANCZOS)
        a = fetch(sc["audio"], f"{tmp}/vo{i}.mp3"); d = duration(a)
        dur = LEAD + d + TAIL
        cs = chunks(sc["text"]); weights = [max(4, len(c)) for c in cs]; tot = sum(weights)
        times = []; acc = LEAD
        for c, wgt in zip(cs, weights):
            seg = d * wgt / tot; times.append((acc, acc + seg, c)); acc += seg
        caps = [(a0, a1, stroke_text(c.upper(), f_cap, YEL if k % 2 == 0 else WHITE)) for k, (a0, a1, c) in enumerate(times)]
        scenes.append(dict(t0=t0, dur=dur, big=big, audio=a, caps=caps, label=sc.get("label"), zoom_in=(i % 2 == 0)))
        t0 += dur
    total = t0
    cv = job["cover"]
    T1 = stroke_text(cv["title1"], f_title1, WHITE, stroke=12, maxw=1000)
    T2 = stroke_text(cv["title2"], f_title2, YEL, stroke=12, maxw=1000)
    B = badge(cv["badge"], f_badge, CYAN, angle=-6)
    HANDLE = stroke_text(job["handle"], f_handle, WHITE, stroke=5, shadow=False)
    LABELS = {i: badge(s["label"], f_lab, YEL) for i, s in enumerate(scenes) if s["label"]}

    def frame(t, cover_mode=False):
        idx = max(i for i, s in enumerate(scenes) if s["t0"] <= t + 1e-6) if t > 0 else 0
        s = scenes[idx]; lt = t - s["t0"]; p = clamp(lt / s["dur"])
        z = 1.0 + 0.10 * (p if s["zoom_in"] else 1 - p)
        bw, bh = int(W * z), int(H * z)
        img = s["big"].resize((bw, bh), Image.BILINEAR) if not cover_mode else s["big"].resize((int(W * 1.0), int(H * 1.0)), Image.LANCZOS)
        ox = (img.width - W) // 2 + int(18 * math.sin(p * math.pi) * (1 if idx % 2 else -1)); oy = (img.height - H) // 2
        ox = max(0, min(img.width - W, ox))
        im = img.crop((ox, oy, ox + W, oy + H)).copy()
        # dark gradients top/bottom for legibility
        g = Image.new("L", (1, H))
        for y in range(H):
            v = 0
            if y < 700: v = int(170 * (1 - y / 700) ** 1.4)
            if y > 1250: v = max(v, int(190 * ((y - 1250) / 670) ** 1.3))
            g.putpixel((0, y), v)
        shade = Image.new("RGBA", (W, H), (0, 0, 0, 255)); shade.putalpha(g.resize((W, H)))
        im.alpha_composite(shade)
        # flash on scene change
        if idx > 0 and lt < 0.12:
            fl = Image.new("RGBA", (W, H), (255, 255, 255, int(150 * (1 - lt / 0.12)))); im.alpha_composite(fl)
        if idx == 0:
            # title is fully visible from frame 0 so the first frame works as the Instagram cover
            pulse = 0 if cover_mode else 0.025 * math.sin(lt * 5)
            put(im, T1, W / 2, 250, 1.0, 1.0 + pulse)
            put(im, T2, W / 2, 430, 1.0, 1.0 + pulse)
            put(im, B, W / 2 + 270, 570, 1.0, 1.0 - pulse)
        elif idx in LABELS:
            k = back(lt / 0.4); put(im, LABELS[idx], W / 2, 170, clamp(k * 2), 0.6 + 0.4 * k)
        if not cover_mode:
            for (a0, a1, spr) in s["caps"]:
                if a0 <= lt < a1:
                    k = back((lt - a0) / 0.18); put(im, spr, W / 2, 1610, 1.0, 0.85 + 0.15 * k)
        put(im, HANDLE, W / 2, 1830, 0.9)
        if not cover_mode:
            d = ImageDraw.Draw(im)
            d.rectangle([0, H - 14, int(W * t / total), H], fill=YEL + (255,))
        return im.convert("RGB")

    cover = frame(0.0, cover_mode=True); cover_path = os.path.join(out_dir, f"{name}_cover.jpg"); cover.save(cover_path, quality=92)
    # audio: voice track with offsets + music ducked
    vo_inputs = []; filt = []
    for i, s in enumerate(scenes):
        vo_inputs += ["-i", s["audio"]]
        ms = int((s["t0"] + LEAD) * 1000); filt.append(f"[{i}:a]adelay={ms}|{ms},aresample=44100[v{i}]")
    n = len(scenes)
    filt.append("".join(f"[v{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0,apad,atrim=0:{total:.3f},loudnorm=I=-15:TP=-1.5[vo]")
    music = job.get("music")
    if music:
        mp = fetch(music, f"{tmp}/music.mp3"); vo_inputs += ["-stream_loop", "-1", "-i", mp]
        g = job.get("music_gain_db", -17)
        filt.append(f"[{n}:a]atrim=0:{total:.3f},volume={g}dB,afade=t=in:d=0.6,afade=t=out:st={total-1.5:.3f}:d=1.5[m]")
        filt.append("[m][vo]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=300[md]")
        filt.append("[md][vo]amix=inputs=2:normalize=0,alimiter=limit=0.95[aout]")
    else:
        filt.append("[vo]anull[aout]")
    # sidechain needs vo twice
    fc = ";".join(filt).replace("loudnorm=I=-15:TP=-1.5[vo]", "loudnorm=I=-15:TP=-1.5,asplit=2[vo][vo2]").replace("[md][vo]amix", "[md][vo2]amix")
    if not music: fc = fc.replace(",asplit=2[vo][vo2]", "[vo]")
    audio_path = os.path.join(tmp, "mix.m4a")
    subprocess.check_call(["ffmpeg", "-y", "-loglevel", "error"] + vo_inputs + ["-filter_complex", fc, "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", audio_path])
    out = os.path.join(out_dir, f"{name}.mp4")
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                          "-i", audio_path, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                          "-profile:v", "high", "-c:a", "copy", "-shortest", "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    N = int(total * FPS)
    for i in range(N):
        p.stdin.write(frame(i / FPS).tobytes())
    p.stdin.close(); p.wait()
    for s_ in [0.5, scenes[1]["t0"] + 1.2 if n > 1 else 1, total - 2]:
        frame(s_).resize((540, 960)).save(os.path.join(out_dir, f"{name}_preview_{s_:.1f}.jpg"), quality=85)
    print(json.dumps({"video": out, "cover": cover_path, "duration": total}))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
