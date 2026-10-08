import asyncio
import os
import re
import time

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

load_dotenv()

import collect  # noqa: E402  (.env를 먼저 읽어야 IG_USER가 보임)
import extract  # noqa: E402

TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ALLOWED = os.getenv("ALLOWED_USER_ID", "").strip()

URL_RE = re.compile(r"https?://\S+")
KIND_NAME = {"youtube": "유튜브", "instagram": "인스타", "web": "웹 글"}
ALBUM_WAIT = 2.0  # 앨범 사진이 다 들어올 때까지 기다리는 시간(초)
PENDING_MINUTES = 30  # 수집 실패한 링크에 캡처를 자동으로 붙여주는 시간

# 수집은 한 번에 하나씩 (인스타 연속 요청 방지)
work_lock = asyncio.Lock()
albums: dict[str, list] = {}
pending = {"url": "", "at": 0.0}  # 마지막으로 수집 실패한 링크


def describe(r: collect.Result) -> str:
    name = KIND_NAME.get(r.kind, r.kind)
    if r.kind == "youtube":
        if not r.ok:
            return (f"유튜브 수집 실패: 링크만 저장했어요\n사유: {r.error}\n"
                    f"필요하면 {PENDING_MINUTES}분 안에 화면 캡처를 보내주세요.")
        sub = f"있음({r.subtitle_lang})" if r.subtitle_lang else "없음 → 음성 변환 필요"
        mins = f"{r.duration // 60}분 {r.duration % 60}초"
        return f"수집 완료 [{name}] {r.title}\n채널: {r.author} · {mins} · 자막 {sub}"
    if r.kind == "instagram":
        if not r.ok:
            return (f"인스타 수집 실패\n사유: {r.error}\n"
                    f"{PENDING_MINUTES}분 안에 캡처를 보내주시면 이 링크로 저장할게요.")
        pics = sum(m.endswith(".jpg") for m in r.media)
        vids = sum(m.endswith(".mp4") for m in r.media)
        return f"수집 완료 [{name}] @{r.author}\n사진 {pics} · 영상 {vids}\n{r.title}"
    if not r.ok:
        return f"본문을 못 읽어서 링크만 저장했어요\n사유: {r.error}"
    return f"수집 완료 [{name}] {r.title}\n본문 {len(r.text)}자"


def describe_extract(r: collect.Result) -> str:
    parts = []
    if r.transcript_chars:
        parts.append(f"음성 글자 {r.transcript_chars}자")
    if r.frames:
        parts.append(f"화면 캡처 {len(r.frames)}장")
    lines = [f"글자 추출 완료: {' · '.join(parts)}"] if parts else []
    # "01.mp4: 말소리 없음" 같은 메모는 종류별로 묶어서 한 줄로
    counts: dict[str, int] = {}
    for n in r.notes:
        reason = n.split(": ", 1)[-1]
        counts[reason] = counts.get(reason, 0) + 1
    for reason, c in counts.items():
        lines.append(f"- {reason}" + (f" (영상 {c}개)" if c > 1 else ""))
    return "\n".join(lines)


async def show_my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 설정 모드: ALLOWED_USER_ID가 비어 있을 때만 동작
    uid = update.effective_user.id
    print(f"받은 ID: {uid}", flush=True)
    await update.message.reply_text(
        f"당신의 ID: {uid}\n.env의 ALLOWED_USER_ID에 넣고 봇을 다시 켜세요."
    )


async def process_link(message, url: str):
    async with work_lock:
        try:
            r = await asyncio.to_thread(collect.collect, url)
            print(f"[{r.kind}] ok={r.ok} {r.url} {r.error}", flush=True)
            if not r.ok and r.kind in ("instagram", "youtube"):
                pending.update(url=r.url, at=time.time())
            await message.reply_text(describe(r), disable_web_page_preview=True)

            if r.ok and r.kind in ("youtube", "instagram"):
                has_video = any(m.endswith(".mp4") for m in r.media)
                if has_video or (r.kind == "youtube" and not r.subtitle_lang):
                    await message.reply_text("영상에서 글자 추출 중... (몇 분 걸릴 수 있어요)")
                r = await asyncio.to_thread(extract.extract, r)
                summary = describe_extract(r)
                print(f"  추출: {summary.replace(chr(10), ' / ')}", flush=True)
                if summary:
                    await message.reply_text(summary)
        except Exception as e:
            print(f"처리 오류 {url}: {e!r}", flush=True)
            await message.reply_text(f"처리 중 오류가 났어요: {e}")


async def log_ignored(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id) if update.effective_user else "?"
    print(f"무시함: 허용 안 된 계정 (ID 끝 ...{uid[-4:]})", flush=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("메시지 받음", flush=True)
    text = update.message.text or update.message.caption or ""
    urls = URL_RE.findall(text)
    if not urls:
        await update.message.reply_text("링크를 찾지 못했어요.")
        return
    for url in urls:
        kind = collect.detect_kind(url)
        await update.message.reply_text(f"받았어요 [{KIND_NAME[kind]}], 처리 중...")
        # 오래 걸리는 수집은 뒤에서 돌리고, 봇은 다음 메시지를 계속 받는다
        context.application.create_task(process_link(update.message, url))


async def process_capture(messages: list):
    first = messages[0]
    caption = "\n".join(m.caption for m in messages if m.caption)
    urls = URL_RE.findall(caption)
    # 링크 정하기: 캡션 링크 > 최근 수집 실패한 링크 > 링크 없음
    if urls:
        url = collect.clean_url(urls[0])
    elif pending["url"] and time.time() - pending["at"] < PENDING_MINUTES * 60:
        url = pending["url"]
    else:
        url = ""
    if url == pending["url"]:
        pending.update(url="", at=0.0)
    note = URL_RE.sub("", caption).strip()
    async with work_lock:
        folder = collect.capture_folder(url)
        for i, m in enumerate(messages[: collect.MAX_MEDIA], 1):
            f = await m.photo[-1].get_file()  # 가장 큰 크기
            await f.download_to_drive(folder / f"{i:02d}.jpg")
        r = collect.save_capture(url, folder, note)
    print(f"[capture] {len(r.media)}장 {url}", flush=True)
    extra = f" (최대 {collect.MAX_MEDIA}장까지만 저장)" if len(messages) > collect.MAX_MEDIA else ""
    where = url or "(원본 링크 없음)"
    await first.reply_text(f"캡처로 저장했어요: {len(r.media)}장{extra}\n{where}",
                           disable_web_page_preview=True)


async def finish_album(gid: str):
    await asyncio.sleep(ALBUM_WAIT)
    messages = albums.pop(gid, [])
    if messages:
        await process_capture(messages)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    gid = msg.media_group_id
    if not gid:
        context.application.create_task(process_capture([msg]))
        return
    # 앨범은 사진이 한 장씩 따로 오므로 잠깐 모았다가 한 번에 처리
    if gid not in albums:
        albums[gid] = []
        context.application.create_task(finish_album(gid))
    albums[gid].append(msg)


def main():
    if not TOKEN:
        raise SystemExit(".env에 TELEGRAM_TOKEN을 먼저 넣어주세요")

    app = Application.builder().token(TOKEN).build()

    if not ALLOWED:
        app.add_handler(MessageHandler(filters.TEXT, show_my_id))
        print("설정 모드: 봇에게 아무 메시지나 보내 ID를 확인하세요.")
    else:
        # 핵심 보안 장치: 허용된 ID에서 온 메시지만 처리, 나머지는 핸들러에 안 걸려서 무시됨
        me_user = filters.User(user_id=int(ALLOWED))
        me = me_user & filters.UpdateType.MESSAGE
        app.add_handler(MessageHandler(me & filters.PHOTO, handle_photo))
        app.add_handler(MessageHandler(me & (filters.TEXT | filters.CAPTION), handle_message))
        # 허용 안 된 계정은 답하지 않고 기록만 남긴다 (차단 확인용)
        app.add_handler(MessageHandler(~me_user, log_ignored), group=1)
        print("봇 실행 중 (Ctrl+C로 종료)")

    app.run_polling()


if __name__ == "__main__":
    main()
