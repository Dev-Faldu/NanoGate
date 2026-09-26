"""Turn the recorded walkthrough into a narrated film: voiceover (Piper, offline), burned-in captions,
title and end cards. Every scene is the real recording; long model waits are sped up and short scenes are held
so each narration line fits its moment. Nothing is simulated.

Usage (from the project root):
  .venv/bin/python frontend/e2e/make_film.py <captures-dir>
Needs: <captures>/video/walkthrough.mp4, <captures>/actions.json, <captures>/card-intro.png, card-outro.png
Writes: <captures>/film/nanogate-demo.mp4, narration.mp3, captions.srt, script.md
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from piper import PiperVoice

CAP = Path(sys.argv[1] if len(sys.argv) > 1 else "captures").resolve()
ROOT = Path(__file__).resolve().parents[2]
FF = imageio_ffmpeg.get_ffmpeg_exe()
VOICE = ROOT / ".runtime/tts/en/en_US/lessac/high/en_US-lessac-high.onnx"
OUT = CAP / "film"
OUT.mkdir(exist_ok=True)
SR = 22050
INTRO_S, OUTRO_S = 7.0, 6.0

INTRO = "This is NanoGate: a local AI gateway on an HP ZGX Nano. Every AI request takes the cheapest safe route, with proof."
OUTRO = "NanoGate. Safer, cheaper, provable AI, on hardware you own."
# (anchor: action, substring of detail) -> narration. Lines are spoken when that action happens in the recording.
LINES = [
    (("click", "Continue"), "An admin signs in to the command center. Every number here is measured on this device."),
    (("hover", "KPI tile"), "Hover any tile or step for a plain-language explanation."),
    (("click", "Try it"), "Let's send real requests through the full pipeline."),
    (("result", "local:"), lambda d: "An IT question is answered from a verified earlier answer, in milliseconds."
     if "CACHE_VERIFIED" in d else "An IT question is answered by the local model on the GB10."),
    (("result", "secret-blocked:"), "A request containing a secret key is blocked before any model sees it."),
    (("result", "privacy-hr:"), "HR personal data is detected, and the answer is generated on this device only."),
    (("result", "kev-rag:"), lambda d: "Security questions are grounded in the CISA vulnerability catalog"
     + (", and reused once verified." if "CACHE_VERIFIED" in d else ".")),
    (("click", "Open receipt"), "Every decision is sealed into a tamper-evident receipt."),
    (("click", "Verify integrity"), "Anyone can verify it. Change one field, and the tamper test catches it."),
    (("open page", "/requests"), "Every request, answered or denied, stays on record."),
    (("click", "New source"), "Company documents make answers specific. This runbook is indexed on the device."),
    (("open page", "/assistant"), "The NanoGate Assistant answers questions from live data, on the local models."),
    (("type", "assistant task"), "It can also prepare tasks, like taking a backup."),
    (("click", "Confirm proposed action"), "Nothing changes until an admin confirms."),
    (("open page", "/access"), "Every app, employee and auditor gets their own key: shown once, revocable any time."),
    (("click", "Departments tab"), "Departments carry their own policies and budgets."),
    (("open page", "/alerts"), "Alerts reach Slack, Teams, or any webhook."),
    (("open page", "/operations"), "Operations covers HTTPS, backups, retention, erasure requests and audit exports."),
    (("click", "By department"), "Finance sees measured cost per department, ready for chargeback."),
    (("open page", "/infrastructure"), "And all of it runs on hardware you own."),
    (("click", "Start chatting"), "Employees get a private assistant that follows their team's rules."),
    (("result", "chat:"), lambda d: "Its answer comes from the company runbook, generated on this device, with a receipt."
     if "runbook" in d.lower() and "Answered on this device" in d else "Every answer comes with a receipt."),
]


def run(*args: str) -> None:
    subprocess.run([FF, "-y", "-loglevel", "error", *args], check=True)


def duration(p: Path) -> float:
    out = subprocess.run([FF, "-i", str(p)], capture_output=True, text=True).stderr
    h, m, s = out.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


voice = PiperVoice.load(str(VOICE))


def tts(text: str) -> np.ndarray:
    tmp = OUT / "_line.wav"
    with wave.open(str(tmp), "wb") as w:
        voice.synthesize_wav(text, w)
    with wave.open(str(tmp)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    tmp.unlink()
    return a


steps = json.loads((CAP / "actions.json").read_text())["steps"]
src = CAP / "video" / "walkthrough.mp4"
total = duration(src)

# anchors in recording time (first match of each line, in order)
anchors, cursor = [], 0
for (act, sub), text in LINES:
    hit = next((i for i in range(cursor, len(steps)) if steps[i]["action"] == act and sub in steps[i]["detail"]), None)
    if hit is None:
        print(f"skip (not in recording): {act} {sub}")
        continue
    anchors.append((min(steps[hit]["t"], total - 0.5), text(steps[hit]["detail"]) if callable(text) else text))
    cursor = hit + 1

# segment plan: [0, a0] prelude, then [a_i, a_i+1] per line; each segment is rendered to its own file and measured,
# so the voice and captions are placed on the real timeline (no drift)
clips = [tts(t) for _, t in anchors]
bounds = [a for a, _ in anchors] + [total]
plan = [(0.0, bounds[0], None)] + [(bounds[i], bounds[i + 1], i) for i in range(len(anchors))]
SEG = OUT / "_seg"
SEG.mkdir(exist_ok=True)
enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30"]
parts = []


def still(img: Path, secs: float, name: str) -> Path:
    out = SEG / name
    run("-loop", "1", "-t", f"{secs:.3f}", "-i", str(img), "-vf", "fps=30,format=yuv420p,setsar=1", *enc, str(out))
    return out


parts.append(still(CAP / "card-intro.png", INTRO_S, "000_intro.mp4"))
marks = []
for k, (s, e, li) in enumerate(plan):
    seg = max(e - s, 0.05)
    need = (len(clips[li]) / SR + 0.5) if li is not None else 0.0
    target = min(seg, 2.5) if li is None else max(need, min(seg, need + 3.0))
    vf = "setpts=PTS-STARTPTS"
    if target < seg:
        vf += f",setpts={target / seg:.6f}*PTS"                          # speed up long waits
    elif target > seg:
        vf += f",tpad=stop_mode=clone:stop_duration={target - seg + 0.1:.3f}"  # hold the frame for the line
    out = SEG / f"{k + 1:03d}.mp4"
    run("-ss", f"{s:.3f}", "-t", f"{seg:.3f}", "-i", str(src), "-vf", vf + ",fps=30,format=yuv420p,setsar=1",
        "-t", f"{target:.3f}", *enc, str(out))
    parts.append(out)
    if li is not None:
        marks.append((len(parts) - 1, li))
parts.append(still(CAP / "card-outro.png", OUTRO_S, "999_outro.mp4"))

durs = [duration(p) for p in parts]
starts = np.cumsum([0.0] + durs[:-1])
film_len = float(sum(durs))
(SEG / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
run("-f", "concat", "-safe", "0", "-i", str(SEG / "list.txt"), "-c", "copy", str(SEG / "body.mp4"))

# audio timeline from measured segment starts
track = np.zeros(int((film_len + 1) * SR), dtype=np.float32)
cues = []
intro_clip, outro_clip = tts(INTRO), tts(OUTRO)
placed = [(0.6, intro_clip, INTRO)] + [(starts[pi] + 0.15, clips[li], anchors[li][1]) for pi, li in marks] + \
         [(starts[-1] + 0.5, outro_clip, OUTRO)]
for start, clip, text in placed:
    i0 = int(start * SR)
    track[i0:i0 + len(clip)] += clip[: max(0, len(track) - i0)]
    cues.append((start, start + len(clip) / SR, text))
track = np.clip(track * 0.95, -1, 1)
with wave.open(str(OUT / "narration.wav"), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((track * 32767).astype(np.int16).tobytes())
run("-i", str(OUT / "narration.wav"), "-c:a", "libmp3lame", "-b:a", "160k", str(OUT / "narration.mp3"))


# captions: SRT (for YouTube/LinkedIn) + ASS (burned in, NanoGate navy box)
def ts(x: float, sep: str = ",") -> str:
    h, r = divmod(x, 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}{sep}{int((s % 1) * 1000):03d}"


def wrap(t: str, n: int = 58) -> list[str]:
    lines, cur = [], ""
    for wd in t.split():
        if len(cur) + len(wd) + 1 > n and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = f"{cur} {wd}".strip()
    return lines + [cur]


(OUT / "captions.srt").write_text("\n".join(f"{i + 1}\n{ts(a)} --> {ts(b)}\n" + "\n".join(wrap(t)) + "\n"
                                             for i, (a, b, t) in enumerate(cues)))
ass = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1440", "PlayResY: 900", "",
       "[V4+ Styles]",
       "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
       "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
       "Style: Cap,DejaVu Sans,27,&H00FFFFFF,&H00FFFFFF,&H1A331B0F,&H1A331B0F,0,0,0,0,100,100,0,0,3,14,0,2,120,120,56,1",
       "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
for a, b, t in cues:
    ass.append(f"Dialogue: 0,{ts(a, '.')[:-1]},{ts(b + 0.25, '.')[:-1]},Cap,,0,0,0,," + "\\N".join(wrap(t)))
(OUT / "captions.ass").write_text("\n".join(ass) + "\n")

run("-i", str(SEG / "body.mp4"), "-i", str(OUT / "narration.wav"),
    "-vf", f"subtitles='{OUT / 'captions.ass'}'", "-map", "0:v", "-map", "1:a",
    "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "160k", "-t", f"{film_len:.3f}", "-movflags", "+faststart", str(OUT / "nanogate-demo.mp4"))

(OUT / "script.md").write_text("# NanoGate demo film: narration script\n\n" +
                               "\n".join(f"- **{ts(a)[:-4]}** {t}" for a, b, t in cues) + "\n")
(OUT / "narration.wav").unlink()
import shutil
shutil.rmtree(SEG)
last_end = max(b for _, b, _ in cues)
print(f"film: {OUT / 'nanogate-demo.mp4'}  ({duration(OUT / 'nanogate-demo.mp4'):.1f} s video, narration ends {last_end:.1f} s, "
      f"{len(cues)} lines)")
