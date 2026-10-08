"""글자 추출: 영상의 음성을 글자로 바꾸고(transcript.txt), 화면을 캡처한다(frames/)."""
import os
import re
import subprocess
from pathlib import Path

import numpy as np
import yt_dlp

import collect

MAX_AUDIO_SEC = 30 * 60   # 자막 없는 영상은 30분 이하만 음성 변환
SHORT_VIDEO_SEC = 3 * 60  # 화면 캡처는 3분 이하 짧은 영상만 (긴 영상은 6장으로 의미 없음)
MAX_FRAMES = 6
FRAME_GAP = 3             # 화면 캡처 최소 간격(초)
WHISPER_MODEL = os.getenv("WHISPER_MODEL") or "small"

_whisper = None


def _model():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper


def _run(args: list):
    subprocess.run(args, capture_output=True, check=True, timeout=600)


def _has_audio(video: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    return bool(out)


def transcribe(video: Path, hint: str = "") -> str:
    # 소리를 ffmpeg로 직접 읽어 넘긴다 (faster-whisper의 파일 읽기는 최신 PyAV와 안 맞음)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-f", "f32le", "-"],
        capture_output=True, check=True, timeout=600,
    ).stdout
    audio = np.frombuffer(raw, dtype=np.float32)
    model = _model()
    # 언어를 먼저 감지한다. 한국어 캡션을 영어 음성에 힌트로 주면 엉뚱한 글자가 나온다
    lang, _, _ = model.detect_language(audio, vad_filter=True)
    hint_lang = "ko" if re.search(r"[가-힣]", hint) else "en"
    # vad_filter: 말이 없는 구간(음악만 나오는 부분)을 건너뛰어 엉뚱한 글자를 줄인다
    # initial_prompt: 같은 언어의 캡션·제목을 힌트로 주면 전문 용어를 훨씬 잘 알아듣는다
    segments, _ = model.transcribe(
        audio, language=lang, vad_filter=True, beam_size=1,
        initial_prompt=(hint[:300] if hint and lang == hint_lang else None),
    )
    # 말소리일 가능성이 낮고 확신도 없는 구간은 버린다 (Whisper 기본 무음 판정 기준)
    lines = [s.text.strip() for s in segments
             if s.text.strip() and not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)]
    return "\n".join(lines)


def capture_frames(video: Path, seconds: int, out_dir: Path, count: int, start_no: int = 1) -> list:
    """영상 전체에 고르게 count장 캡처. 3초 간격 기준으로 짧은 영상은 장수를 줄인다."""
    count = max(1, min(count, seconds // FRAME_GAP or 1))
    out_dir.mkdir(exist_ok=True)
    names = []
    for i in range(count):
        t = seconds * (i + 0.5) / count
        name = f"f{start_no + i:02d}.jpg"
        try:
            _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.1f}", "-i", str(video),
                  "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "4", str(out_dir / name)])
            names.append(name)
        except subprocess.CalledProcessError:
            pass
    return names


def _download_youtube(r: collect.Result, folder: Path, with_video: bool) -> Path | None:
    fmt = "bv*[height<=480]+ba/b[height<=480]/b" if with_video else "ba/b"
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "format": fmt,
            "outtmpl": str(folder / "video.%(ext)s")}
    if with_video:
        opts["merge_output_format"] = "mp4"
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([r.url])
    files = [p for p in folder.glob("video.*") if p.suffix != ".part"]
    return files[0] if files else None


def extract(r: collect.Result) -> collect.Result:
    if not r.ok or r.kind not in ("youtube", "instagram"):
        return r
    folder = Path(r.folder)
    r.notes = []
    videos = []  # (파일, 길이, 음성변환 할지)

    if r.kind == "youtube":
        need_audio = not r.subtitle_lang
        if need_audio and r.duration > MAX_AUDIO_SEC:
            r.notes.append("자막 없고 30분 넘어서 음성 변환 생략")
            need_audio = False
        need_frames = 0 < r.duration <= SHORT_VIDEO_SEC
        if need_audio or need_frames:
            try:
                v = _download_youtube(r, folder, with_video=need_frames)
                if v:
                    videos.append((v, r.duration, need_audio))
            except Exception as e:
                r.notes.append(f"영상 받기 실패: {collect._short_error(e)}")
    else:
        for m in r.media:
            if m.endswith(".mp4"):
                v = folder / m
                videos.append((v, collect._video_seconds(v), True))

    texts, frames = [], []
    per_video = max(1, MAX_FRAMES // len(videos)) if videos else 0
    for idx, (v, sec, do_audio) in enumerate(videos, 1):
        label = f"[영상 {idx}]\n" if len(videos) > 1 else ""
        if do_audio:
            if not _has_audio(v):
                r.notes.append(f"{v.name}: 소리 없음")
            elif sec > MAX_AUDIO_SEC:
                r.notes.append(f"{v.name}: 30분 넘어서 음성 변환 생략")
            else:
                try:
                    t = transcribe(v, hint=f"{r.title}\n{r.text}" if r.kind == "youtube" else r.text)
                    if t:
                        texts.append(label + t)
                    else:
                        r.notes.append(f"{v.name}: 말소리 없음")
                except Exception as e:
                    r.notes.append(f"{v.name}: 음성 변환 실패 {collect._short_error(e)}")
        if v.suffix == ".mp4" and 0 < sec <= SHORT_VIDEO_SEC and len(frames) < MAX_FRAMES:
            frames += capture_frames(v, sec, folder / "frames",
                                     min(per_video, MAX_FRAMES - len(frames)), len(frames) + 1)
        if r.kind == "youtube" and not v.suffix == ".mp4":
            v.unlink(missing_ok=True)  # 음성만 받은 파일은 바로 지운다

    if texts:
        transcript = "\n\n".join(texts)
        (folder / "transcript.txt").write_text(transcript, encoding="utf-8")
        r.transcript_chars = len(transcript)
    r.frames = frames
    return collect._save(r, folder)
