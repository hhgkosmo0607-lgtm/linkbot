"""디스코드 버전 봇: bot.py(텔레그램)와 똑같이 동작한다. 수집·추출은 collect.py, extract.py를 그대로 쓴다."""
import asyncio
import os
import time
from io import BytesIO
from pathlib import Path

import discord
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

import collect  # noqa: E402  (.env를 먼저 읽어야 IG_USER가 보임)
import extract  # noqa: E402
# 답장 문구는 텔레그램 봇과 같은 걸 쓴다 (bot.py는 import만 해서는 실행되지 않음)
from bot import KIND_NAME, PENDING_MINUTES, URL_RE, describe, describe_extract  # noqa: E402

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
ALLOWED = os.getenv("DISCORD_ALLOWED_USER_ID", "").strip()

MAX_LEN = 2000  # 디스코드 메시지 글자 수 제한
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# 수집은 한 번에 하나씩 (인스타 연속 요청 방지)
work_lock = asyncio.Lock()
pending = {"url": "", "at": 0.0}  # 마지막으로 수집 실패한 링크
tasks: set = set()  # 뒤에서 돌리는 작업이 중간에 사라지지 않게 잡아둔다

intents = discord.Intents.default()
intents.message_content = True  # 메시지 내용 읽기 (개발자 포털에서도 켜야 함)
intents.dm_messages = True
client = discord.Client(intents=intents)


def run_later(coro):
    t = asyncio.create_task(coro)
    tasks.add(t)
    t.add_done_callback(tasks.discard)


async def reply(message, text: str):
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1] + "…"
    # suppress_embeds: 링크 미리보기 끄기 (텔레그램의 disable_web_page_preview)
    await message.reply(text, mention_author=False, suppress_embeds=True)


def is_image(a) -> bool:
    ct = (a.content_type or "").lower()
    return ct.startswith("image/") or Path(a.filename).suffix.lower() in IMG_EXT


def save_jpg(data: bytes, path: Path) -> bool:
    """뒤 단계가 .jpg만 보므로 png·webp 등은 jpg로 바꿔 저장한다."""
    if data[:3] == b"\xff\xd8\xff":  # 이미 jpg
        path.write_bytes(data)
        return True
    try:
        with Image.open(BytesIO(data)) as im:
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")  # 투명한 부분은 흰 바탕으로
            bg.paste(im, mask=im.getchannel("A"))
            bg.save(path, "JPEG", quality=90)
        return True
    except Exception:
        return False


def pick_capture_url(text: str) -> str:
    # 링크 정하기: 메시지 링크 > 최근 수집 실패한 링크 > 링크 없음
    urls = URL_RE.findall(text)
    if urls:
        url = collect.clean_url(urls[0])
    elif pending["url"] and time.time() - pending["at"] < PENDING_MINUTES * 60:
        url = pending["url"]
    else:
        url = ""
    if url == pending["url"]:
        pending.update(url="", at=0.0)
    return url


async def process_link(message, url: str):
    async with work_lock:
        try:
            r = await asyncio.to_thread(collect.collect, url)
            print(f"[{r.kind}] ok={r.ok} {r.url} {r.error}", flush=True)
            if not r.ok and r.kind in ("instagram", "youtube"):
                pending.update(url=r.url, at=time.time())
            await reply(message, describe(r))

            if r.ok and r.kind in ("youtube", "instagram"):
                has_video = any(m.endswith(".mp4") for m in r.media)
                if has_video or (r.kind == "youtube" and not r.subtitle_lang):
                    await reply(message, "영상에서 글자 추출 중... (몇 분 걸릴 수 있어요)")
                r = await asyncio.to_thread(extract.extract, r)
                summary = describe_extract(r)
                print(f"  추출: {summary.replace(chr(10), ' / ')}", flush=True)
                if summary:
                    await reply(message, summary)
        except Exception as e:
            print(f"처리 오류 {url}: {e!r}", flush=True)
            await reply(message, f"처리 중 오류가 났어요: {e}")


async def process_capture(message, images: list):
    # 디스코드는 여러 장을 한 메시지에 첨부로 보내므로 앨범 모으기가 필요 없다
    text = message.content or ""
    url = pick_capture_url(text)
    note = URL_RE.sub("", text).strip()
    failed = 0
    async with work_lock:
        folder = collect.capture_folder(url)
        n = 0
        for a in images[: collect.MAX_MEDIA]:
            data = await a.read()
            if await asyncio.to_thread(save_jpg, data, folder / f"{n + 1:02d}.jpg"):
                n += 1
            else:
                failed += 1
        r = collect.save_capture(url, folder, note)
    print(f"[capture] {len(r.media)}장 {url}", flush=True)
    extra = f" (최대 {collect.MAX_MEDIA}장까지만 저장)" if len(images) > collect.MAX_MEDIA else ""
    if failed:
        extra += f"\n{failed}장은 이미지로 읽지 못해서 뺐어요"
    where = url or "(원본 링크 없음)"
    await reply(message, f"캡처로 저장했어요: {len(r.media)}장{extra}\n{where}")


async def handle_message(message):
    print("메시지 받음", flush=True)
    images = [a for a in message.attachments if is_image(a)]
    if images:
        run_later(process_capture(message, images))
        return
    urls = URL_RE.findall(message.content or "")
    if not urls:
        await reply(message, "링크를 찾지 못했어요.")
        return
    for url in urls:
        kind = collect.detect_kind(url)
        await reply(message, f"받았어요 [{KIND_NAME[kind]}], 처리 중...")
        # 오래 걸리는 수집은 뒤에서 돌리고, 봇은 다음 메시지를 계속 받는다
        run_later(process_link(message, url))


@client.event
async def on_ready():
    print(f"로그인: {client.user}", flush=True)
    if not ALLOWED:
        print("설정 모드: 봇에게 DM으로 아무 메시지나 보내 ID를 확인하세요.", flush=True)
    else:
        print("봇 실행 중 (Ctrl+C로 종료)", flush=True)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:  # 봇 자신(과 다른 봇)의 메시지는 무시
        return
    uid = str(message.author.id)

    if not ALLOWED:
        # 설정 모드: DISCORD_ALLOWED_USER_ID가 비어 있을 때 DM에만 ID를 알려준다
        if isinstance(message.channel, discord.DMChannel):
            print(f"받은 ID: {uid}", flush=True)
            await reply(message, f"당신의 ID: {uid}\n.env의 DISCORD_ALLOWED_USER_ID에 넣고 봇을 다시 켜세요.")
        return

    # 핵심 보안 장치: 허용된 ID만 처리. 나머지는 답하지 않고 기록만 남긴다
    if uid != ALLOWED:
        print(f"무시함: 허용 안 된 계정 (ID 끝 ...{uid[-4:]})", flush=True)
        return
    await handle_message(message)


def main():
    if not TOKEN:
        raise SystemExit(".env에 DISCORD_TOKEN을 먼저 넣어주세요")
    try:
        client.run(TOKEN)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("개발자 포털 Bot 화면에서 MESSAGE CONTENT INTENT를 켜고 다시 실행하세요")
    except discord.LoginFailure:
        raise SystemExit("DISCORD_TOKEN이 틀렸어요. 앞뒤 공백·따옴표를 확인하세요")


if __name__ == "__main__":
    main()
