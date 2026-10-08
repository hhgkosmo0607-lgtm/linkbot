"""AI 요약·분류: 수집·추출한 재료를 Claude Code(구독)로 보내 제목·요약·폴더·태그를 받는다."""
import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv

import collect

load_dotenv()
# .env에 빈칸으로 둔 항목은 기본값을 쓴다
MODEL = os.getenv("CLAUDE_MODEL") or "sonnet"
VAULT = Path(os.getenv("VAULT_DIR") or "vault")
LINK_DIR = "링크"
MAX_IMAGES = 10
MAX_TEXT = 20000  # 글이 너무 길면 이만큼만 보낸다 (한도 아끼기)
TIMEOUT = 300

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "folder": {"type": "string"},
        "is_new_folder": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "key_points", "folder", "is_new_folder", "tags"],
    "additionalProperties": False,
}

SYSTEM = """너는 저장한 링크를 정리해 주는 도우미다. 받은 글과 이미지를 읽고 한국어로 정리한다.

- title: 내용이 바로 떠오르는 노트 제목. 40자 이내. 원래 제목이 낚시성이면 내용대로 바꾼다
- summary: 3~5문장 요약. 무엇을 알려주는지, 왜 쓸모 있는지
- key_points: 나중에 바로 써먹을 핵심 3~7개. 숫자·이름·방법은 구체적으로
- folder: 주제 폴더 이름
  - 기존 폴더 중 주제가 맞는 게 있으면 반드시 그 이름을 그대로 쓴다
  - 기존 폴더 어느 것과도 겹치지 않을 때만 새 폴더를 만든다
  - 새 폴더는 2~12자의 짧은 한국어 주제어 (예: 결제 개발, 숏폼 편집). 너무 좁거나 넓지 않게
- is_new_folder: 새로 만든 폴더면 true
- tags: 2~5개, # 없이

이미지 속 글자(카드뉴스, 화면 자막, 코드)도 내용에 포함해서 정리한다.
음성 받아쓰기는 잘못 들은 단어가 있을 수 있으니 캡션·화면과 맞춰서 바로잡는다."""


class SummaryError(Exception):
    def __init__(self, msg: str, limited: bool = False):
        super().__init__(msg)
        self.limited = limited  # 구독 한도에 걸린 경우 → 나중에 다시 처리


def _claude_bin() -> str:
    if os.getenv("CLAUDE_BIN"):
        return os.getenv("CLAUDE_BIN")
    found = shutil.which("claude") or ""
    # 윈도우의 claude.cmd/ps1는 실제 exe를 다시 부르는 껍데기라 인자 따옴표가 깨진다 → exe 직접 사용
    exe = Path(found).parent / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    if exe.exists():
        return str(exe)
    if found:
        return found
    raise SummaryError("claude 명령어를 찾을 수 없음 (.env에 CLAUDE_BIN 지정)")


def existing_folders() -> list[str]:
    base = VAULT / LINK_DIR
    if not base.exists():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith("."))


def _key(name: str) -> str:
    return re.sub(r"[\s_\-·.]", "", name).lower()


def clean_folder(name: str, folders: list[str]) -> tuple[str, bool]:
    """AI가 고른 폴더를 다듬고, 띄어쓰기만 다른 기존 폴더가 있으면 그쪽으로 합친다."""
    name = re.sub(r'[\\/:*?"<>|#]', " ", name).strip(" .") or "기타"
    name = re.sub(r"\s+", " ", name)[:20]
    for f in folders:
        if _key(f) == _key(name):
            return f, False
    return name, True


def _images(r: collect.Result) -> list[Path]:
    folder = Path(r.folder)
    originals = [folder / m for m in r.media if m.endswith(".jpg")]
    frames = [folder / "frames" / f for f in r.frames]
    # 원본 사진 먼저, 남는 자리에 영상 화면 캡처
    return [p for p in originals + frames if p.exists()][:MAX_IMAGES]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n(이하 생략)"


def build_content(r: collect.Result, folders: list[str]) -> list:
    folder = Path(r.folder)
    source = {"youtube": "유튜브", "instagram": "인스타그램", "web": "웹 글", "capture": "캡처"}[r.kind]
    parts = [
        f"기존 폴더: {', '.join(folders) if folders else '(아직 없음)'}",
        f"출처: {source}" + (f" / 작성자 {r.author}" if r.author else ""),
        f"주소: {r.url or '(없음)'}",
    ]
    if r.title:
        parts.append(f"원래 제목: {r.title}")
    if r.text:
        label = {"web": "본문", "youtube": "설명", "capture": "메모"}.get(r.kind, "캡션")
        parts.append(f"{label}:\n{_clip(r.text)}")
    if sub := _read(folder / "subtitle.txt"):
        parts.append(f"자막:\n{_clip(sub)}")
    if tr := _read(folder / "transcript.txt"):
        parts.append(f"음성 받아쓰기:\n{_clip(tr)}")
    if r.kind == "capture":
        parts.append("첨부 이미지는 사용자가 보낸 화면 캡처다. 이미지 내용이 핵심이다.")

    content = []
    for img in _images(r):
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(img.read_bytes()).decode()}})
    content.append({"type": "text", "text": "\n\n".join(parts) + "\n\n위 내용을 정리해줘."})
    return content


def ask_claude(content: list) -> tuple[dict, dict]:
    cmd = [
        _claude_bin(), "-p", "--model", MODEL,
        "--safe-mode",            # CLAUDE.md·플러그인 등 개인 설정을 읽지 않는다 (한도 절약)
        "--tools", "",            # 도구 없이 요약만
        "--strict-mcp-config", "--no-session-persistence",
        "--system-prompt", SYSTEM,
        "--json-schema", json.dumps(SCHEMA, ensure_ascii=False),
        "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
    ]
    env = os.environ.copy()
    # API 키가 있으면 구독 대신 API로 결제되므로 빼고 넘긴다
    env.pop("ANTHROPIC_API_KEY", None)
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": content}},
                     ensure_ascii=False)
    try:
        p = subprocess.run(cmd, input=msg + "\n", capture_output=True, text=True,
                           encoding="utf-8", timeout=TIMEOUT, env=env,
                           cwd=os.getenv("TEMP") or None)
    except subprocess.TimeoutExpired:
        raise SummaryError(f"{TIMEOUT}초 안에 응답 없음")

    result = None
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev
    if not result:
        raise SummaryError(f"응답 없음: {(p.stderr or '').strip()[-300:]}")
    if result.get("is_error") or not result.get("structured_output"):
        text = str(result.get("result") or "")
        limited = bool(re.search(r"limit|rate|429|사용량", text, re.I))
        raise SummaryError(text[:300] or "알 수 없는 오류", limited=limited)

    usage = {
        "model": next(iter(result.get("modelUsage") or {}), MODEL),
        "cost_usd": result.get("total_cost_usd"),  # API로 냈다면 들었을 금액 (한도 사용량 가늠용)
        "input_tokens": (result.get("usage") or {}).get("input_tokens"),
        "output_tokens": (result.get("usage") or {}).get("output_tokens"),
        "seconds": round((result.get("duration_ms") or 0) / 1000),
    }
    return result["structured_output"], usage


def summarize(r: collect.Result) -> dict:
    folders = existing_folders()
    data, usage = ask_claude(build_content(r, folders))
    data["folder"], data["is_new_folder"] = clean_folder(data["folder"], folders)
    data["tags"] = [t.lstrip("#").strip() for t in data.get("tags", []) if t.strip()][:5]
    data["usage"] = usage
    (Path(r.folder) / "summary.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load_result(folder: Path) -> collect.Result:
    return collect.Result(**json.loads((folder / "meta.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    # 봇 없이 시험: python summarize.py data/collected/폴더이름
    import sys
    data = summarize(load_result(Path(sys.argv[1])))
    print(json.dumps(data, ensure_ascii=False, indent=2))
