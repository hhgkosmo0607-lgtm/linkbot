import os
import re
import sys
from pathlib import Path

import instaloader
import trafilatura
import yt_dlp
from dotenv import load_dotenv

load_dotenv()
IG_USER = os.getenv("IG_USER", "").strip()
OUT = Path("test_output")
OUT.mkdir(exist_ok=True)


def test_youtube(url):
    opts = {
        "skip_download": True,          # 영상은 받지 않고 정보·자막만
        "writesubtitles": True,
        "writeautomaticsub": True,      # 자동 생성 자막까지
        "subtitleslangs": ["ko", "en"],
        "outtmpl": str(OUT / "yt_%(id)s.%(ext)s"),
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    subs = list(OUT.glob(f"yt_{info['id']}*.vtt"))
    print(f"제목: {info['title']}")
    print(f"설명 길이: {len(info.get('description') or '')}자")
    print(f"자막 파일: {[s.name for s in subs] or '없음'}")


def test_instagram(url):
    if not IG_USER:
        print(".env에 IG_USER(부계정 아이디)를 먼저 적어주세요")
        return
    # 링크에서 게시물 고유 코드만 뽑기 (/p/코드/ 또는 /reel/코드/)
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels)/([^/?]+)", url)
    if not m:
        print("인스타 링크 형식이 아님")
        return
    shortcode = m.group(1)

    # 경로는 dirname_pattern에 넣는다. target에 넣으면 윈도우에서 "\"가 다른 글자로 바뀜
    L = instaloader.Instaloader(
        dirname_pattern=str(OUT / "ig_{target}"),
        download_videos=True,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        quiet=True,
    )
    L.load_session_from_file(IG_USER)
    post = instaloader.Post.from_shortcode(L.context, shortcode)
    L.download_post(post, target=shortcode)

    # 캡션 txt 등은 빼고 이미지·영상만 센다
    target = OUT / f"ig_{shortcode}"
    images = list(target.glob("*.jpg"))
    videos = list(target.glob("*.mp4"))
    print(f"종류: {'영상' if post.is_video else '이미지/캐러셀'}, 장수: {post.mediacount}")
    print(f"캡션 앞부분: {(post.caption or '')[:80]}")
    print(f"받은 파일: 이미지 {len(images)}개, 영상 {len(videos)}개 (합계 {len(images) + len(videos)})")


def test_web(url):
    html = trafilatura.fetch_url(url)
    text = trafilatura.extract(html) if html else None
    if not text:
        print("본문 추출 실패")
        return
    print(f"본문 길이: {len(text)}자")
    print(f"앞부분: {text[:100]}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("yt", "ig", "web"):
        print('사용법: python test_collect.py [yt|ig|web] "링크"')
        sys.exit(1)
    kind, url = sys.argv[1], sys.argv[2]
    {"yt": test_youtube, "ig": test_instagram, "web": test_web}[kind](url)
