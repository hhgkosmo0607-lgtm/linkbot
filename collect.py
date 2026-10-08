"""링크 수집: 유튜브·인스타·웹 내용을 받아서 data/collected/ 아래 폴더에 저장한다."""
import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import instaloader
import trafilatura
import yt_dlp

DATA = Path("data/collected")
MAX_MEDIA = 10  # 링크당 이미지·영상 최대 개수

IG_RE = re.compile(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/([^/?#]+)")


@dataclass
class Result:
    url: str
    kind: str                    # youtube / instagram / web / capture
    ok: bool = False
    title: str = ""
    author: str = ""
    text: str = ""               # 설명·캡션·본문
    subtitle_lang: str = ""      # 자막 언어 (유튜브)
    duration: int = 0            # 영상 길이(초)
    media: list = field(default_factory=list)  # 저장한 파일 이름들
    transcript_chars: int = 0    # 음성 → 글자 결과 길이 (transcript.txt)
    frames: list = field(default_factory=list)  # 영상 화면 캡처 파일들
    notes: list = field(default_factory=list)   # 추출 중 생략·실패 사유
    error: str = ""
    folder: str = ""
    collected_at: str = ""


def detect_kind(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    return "web"


def clean_url(url: str) -> str:
    """공유 추적값(?stkn=, ?si=, utm_ 등)을 떼서 같은 글이면 같은 주소가 되게 한다."""
    url = url.strip().rstrip(".,!?")
    # 문장 끝 괄호만 뗀다. 위키처럼 주소 안에 괄호가 있으면 짝이 맞으니 둔다
    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    p = urlparse(url)
    kind = detect_kind(url)
    if kind == "instagram":
        m = IG_RE.search(url)
        if m:
            return f"https://www.instagram.com/p/{m.group(1)}/"
    if kind == "youtube":
        q = parse_qs(p.query)
        if "v" in q:
            return f"https://www.youtube.com/watch?v={q['v'][0]}"
        return urlunparse(p._replace(query="", fragment=""))
    q = [(k, v) for k, vs in parse_qs(p.query).items() for v in vs if not k.startswith("utm_")]
    return urlunparse(p._replace(query=urlencode(q), fragment=""))


def _short_error(e: Exception) -> str:
    return str(e).strip().splitlines()[0][:200] if str(e).strip() else type(e).__name__


def _make_folder(kind: str, key: str) -> Path:
    folder = DATA / f"{datetime.now():%Y-%m-%d}_{kind}_{key}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _save(r: Result, folder: Path) -> Result:
    r.folder = str(folder)
    r.collected_at = datetime.now().isoformat(timespec="seconds")
    (folder / "meta.json").write_text(
        json.dumps(asdict(r), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return r


# ---------- 유튜브 ----------

def _vtt_to_text(vtt: str) -> str:
    lines = []
    for line in vtt.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
            continue
        if line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        # 자동 자막은 같은 줄이 여러 번 반복돼서 바로 앞 줄과 같으면 뺀다
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return "\n".join(lines)


def _pick_subtitle(info: dict):
    """수동 자막(ko→en) 우선, 없으면 원어 자동 자막. 번역 자막은 피한다(요청 차단 위험)."""
    manual = info.get("subtitles") or {}
    for lang in ("ko", "en"):
        if lang in manual:
            return lang, manual[lang]
    auto = info.get("automatic_captions") or {}
    orig = info.get("language")
    candidates = [k for k in auto if k.endswith("-orig")]
    if orig:
        candidates.append(orig)
    candidates += ["ko", "en"]
    for lang in candidates:
        if lang in auto:
            return lang, auto[lang]
    return None, None


def collect_youtube(url: str) -> Result:
    r = Result(url=url, kind="youtube")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            r.url = f"https://www.youtube.com/watch?v={info['id']}"
            r.title = info.get("title") or ""
            r.author = info.get("channel") or info.get("uploader") or ""
            r.text = info.get("description") or ""
            r.duration = int(info.get("duration") or 0)
            folder = _make_folder("youtube", info["id"])

            lang, formats = _pick_subtitle(info)
            if formats:
                vtt = next((f for f in formats if f.get("ext") == "vtt"), None)
                if vtt:
                    raw = ydl.urlopen(vtt["url"]).read().decode("utf-8", "replace")
                    (folder / "subtitle.txt").write_text(_vtt_to_text(raw), encoding="utf-8")
                    r.subtitle_lang = lang
                    r.media.append("subtitle.txt")
        r.ok = True
    except Exception as e:
        r.error = _short_error(e)
        m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url)
        folder = _make_folder("youtube", m.group(1) if m else _hash(url))
    return _save(r, folder)


# ---------- 인스타 ----------

def _video_seconds(path: Path) -> int:
    """인스타가 길이를 안 줄 때가 있어서 받은 파일에서 직접 잰다."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return int(float(out))
    except Exception:
        return 0


_loader = None


def _ig_loader() -> instaloader.Instaloader:
    global _loader
    if _loader is None:
        user = os.getenv("IG_USER", "").strip()
        if not user:
            raise RuntimeError(".env에 IG_USER가 없음")
        _loader = instaloader.Instaloader(
            quiet=True,
            save_metadata=False,
            iphone_support=False,       # 고화질 API는 403이 나서 끈다
            max_connection_attempts=1,  # 실패해도 반복 요청하지 않는다
        )
        _loader.load_session_from_file(user)
    return _loader


def collect_instagram(url: str) -> Result:
    r = Result(url=url, kind="instagram")
    m = IG_RE.search(url)
    if not m:
        r.error = "인스타 게시물 링크 형식이 아님"
        return _save(r, _make_folder("instagram", _hash(url)))
    code = m.group(1)
    folder = _make_folder("instagram", code)
    try:
        L = _ig_loader()
        post = instaloader.Post.from_shortcode(L.context, code)
        r.author = post.owner_username
        r.text = post.caption or ""
        r.title = (r.text.splitlines() or [""])[0][:60]

        if post.typename == "GraphSidecar":
            items = [(n.is_video, n.video_url if n.is_video else n.display_url)
                     for n in post.get_sidecar_nodes()]
        else:
            items = [(post.is_video, post.video_url if post.is_video else post.url)]
            if post.is_video:
                r.duration = int(post.video_duration or 0)

        for i, (is_video, media_url) in enumerate(items[:MAX_MEDIA], 1):
            if not media_url:
                continue
            L.download_pic(str(folder / f"{i:02d}"), media_url, post.date_utc)
        r.media = sorted(p.name for p in folder.iterdir() if p.suffix in (".jpg", ".mp4", ".webp"))
        if r.duration == 0:
            r.duration = sum(_video_seconds(folder / m) for m in r.media if m.endswith(".mp4"))
        r.ok = bool(r.media)
        if not r.ok:
            r.error = "파일을 하나도 받지 못함"
    except Exception as e:
        r.error = _short_error(e)
    return _save(r, folder)


# ---------- 웹 ----------

def _hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:10]


def collect_web(url: str) -> Result:
    r = Result(url=url, kind="web")
    folder = _make_folder("web", _hash(url))
    try:
        html = trafilatura.fetch_url(url)
        if not html:
            raise RuntimeError("페이지를 받지 못함")
        meta = trafilatura.extract_metadata(html)
        r.title = (meta.title if meta and meta.title else "") or url
        r.author = (meta.sitename or meta.author or "") if meta else ""
        r.text = trafilatura.extract(html) or ""
        r.ok = len(r.text) >= 200
        if not r.ok:
            r.error = "본문이 없거나 너무 짧음"
    except Exception as e:
        r.error = _short_error(e)
    return _save(r, folder)


# ---------- 캡처 대체 경로 ----------

def capture_folder(url: str) -> Path:
    if not url:
        return _make_folder("capture", f"{datetime.now():%H%M%S}")
    kind = detect_kind(url)
    m = IG_RE.search(url)
    key = m.group(1) if (kind == "instagram" and m) else _hash(url)
    return _make_folder(f"capture-{kind}", key)


def save_capture(url: str, folder: Path, note: str) -> Result:
    r = Result(url=url, kind="capture", ok=True, text=note)
    r.media = sorted(p.name for p in folder.iterdir() if p.suffix == ".jpg")
    return _save(r, folder)


def collect(url: str) -> Result:
    url = clean_url(url)
    kind = detect_kind(url)
    return {"youtube": collect_youtube, "instagram": collect_instagram, "web": collect_web}[kind](url)
