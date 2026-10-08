# 링크 정리 봇

인스타·유튜브·웹 링크를 텔레그램으로 보내면 내용을 수집·요약해서 옵시디언 노트로 저장하는 개인용 봇.
나중에 연결 제안, 실행 키트 기능을 붙인다.

## 문서
- `docs/아이디어.md` — 전체 아이디어와 방향성
- `docs/설계서.md` — MVP 범위, 구조, 기술 선택, 처리 흐름 (**작업 기준 문서**)
- `docs/제작법/` — 단계별 만드는 법 (`1단계_PC준비_수집테스트.md` ~ `4단계_글자추출.md`, `디스코드봇.md`)
- `docs/작업현황.md` — 단계별 상태, 막힌 것, 고칠 것, 다음 할 것
- `docs/미니PC_필요성.md` — 미니 PC가 필요한 이유, 고르는 기준

작업 전에 설계서를 먼저 읽고, 설계서와 다르게 구현해야 할 때는 이유를 말하고 확인받을 것.

## 현재 상태
- 설계서 v0.2 확정 (실행 장소를 집 PC로 변경), MVP 구현 중
- **다음 작업과 고칠 것은 `docs/작업현황.md`에 정리되어 있다. 작업 시작 전에 먼저 읽을 것**
- 1단계 통과 (2026-10-08): 유튜브(자막)·웹·인스타 게시물(캐러셀 7개)·릴스(720x1280, 음성 포함) 모두 성공
  - 인스타 고화질 API는 403 login_required → instaloader가 일반 화질로 대체 (요약용으로 충분)
  - 윈도우에서 instaloader 저장 경로는 `target`이 아닌 `dirname_pattern`에 넣어야 함
- 2단계 통과: `bot.py` 동작, 내 계정 잠금, 인스타·유튜브 공유 인식 (다른 계정 차단 확인은 생략)
- 3단계 통과 (2026-10-08): 폰에서 인스타 릴스 수집, 캡션 없는 캡처 10장 저장 확인. 유튜브·웹은 PC에서 확인
  - 캡처는 링크 없이 보내도 됨 (캡션 링크 > 30분 내 실패 링크 > 링크 없음)
  - 인스타 영상 길이는 ffprobe로 직접 잰다 (고화질 API를 끄면 길이 정보가 0)
- 4단계 통과 (2026-10-08): `extract.py` (음성 → `transcript.txt`, 화면 → `frames/`). 폰에서 영상 7개 캐러셀 확인
  - Whisper small + 캡션 힌트가 기본 (`.env`의 `WHISPER_MODEL`로 변경). 134초 릴스 이 PC에서 35초
  - 언어를 먼저 감지하고, 캡션과 같은 언어일 때만 힌트를 준다 (한국어 힌트 + 영어 음성 → 엉뚱한 글자)
  - 무음 판정(no_speech>0.6 이고 logprob<-1)인 구간은 버린다
- 5단계 진행 중: `summarize.py` (Claude Code 구독으로 요약, `claude -p --safe-mode --tools "" --json-schema`)
  - API 대신 구독 사용 (사용자 결정). 자식 프로세스에서 `ANTHROPIC_API_KEY`를 빼야 구독으로 결제됨
  - 윈도우는 `claude.cmd`가 JSON 인자 따옴표를 깨뜨려서 `node_modules/@anthropic-ai/claude-code/bin/claude.exe`를 직접 실행
  - 인증은 `claude setup-token` 장기 토큰을 `.env`의 `CLAUDE_CODE_OAUTH_TOKEN`에
- 텔레그램: 재활용 번호라 이전 주인이 2단계 인증을 걸어 계정이 막힘 (2026-10-08). 7일 뒤 초기화 예정. 그동안 봇은 꺼 둠
- 봇 로그: `logs/bot.log`, `logs/error.log`. 봇은 `Start-Process`로 창 없이 실행 중 (재부팅하면 꺼짐, 7단계에서 자동 실행)
  - faster-whisper의 파일 읽기는 최신 PyAV와 안 맞아서 ffmpeg로 소리를 직접 읽어 넘긴다
- 디스코드 버전: `discord_bot.py` (텔레그램 막힌 동안 대체). 오프라인 시험만 통과, 실제 접속은 아직. 만드는 법 `docs/제작법/디스코드봇.md`
  - `bot.py`의 `describe` 등은 가져다 쓰지만 `process_link`·`process_capture`는 복사본 → `bot.py` 흐름을 바꾸면 여기도 같이 고칠 것
- 파일: `collect.py`, `extract.py`, `summarize.py`, `bot.py`, `discord_bot.py`, `test_collect.py`(1단계용), `.env.example`, `.gitignore`. 수집 결과는 `data/collected/`

## 환경
- 실행: 집 PC(윈도우 10, PowerShell). 개발은 지금 PC에서, 상시 구동은 나중에 N100급 미니 PC로 폴더째 이전. 파이썬 3.12, 가상환경 `venv/`
- 폰: 안드로이드 (옵시디언 Git 플러그인으로 동기화)
- 주요 도구: python-telegram-bot, yt-dlp(+Deno), instaloader, trafilatura, ffmpeg, faster-whisper, Claude Code(구독, `claude -p`), SQLite

## 반드시 지킬 규칙
- 비밀 정보(텔레그램 토큰, API 키, 인스타 로그인)는 `.env`와 세션 파일에만 둔다. 코드나 깃에 절대 넣지 않는다
- 봇은 `.env`의 `ALLOWED_USER_ID` 계정 메시지에만 반응한다
- 인스타는 부계정으로만 접속하고, 테스트도 링크당 1~2회로 제한한다. 반복 요청 코드를 만들지 않는다
- 봇은 옵시디언 vault의 `링크/`, `첨부/` 폴더에만 쓴다. `내메모/`, `00_내목표.md`는 건드리지 않는다
- 링크당 이미지 최대 10장, 영상 화면 캡처 최대 6장, 자막 없는 영상 음성 변환은 30분 이하만

## 작업 방식
- 설계서의 제작 단계 순서대로 진행한다 (1 → 7)
- 단계마다 만드는 법 문서를 `docs/제작법/N단계_주제.md`로 남긴다. 제목은 `# 제작법 N단계 — 주제`. '교안'이라는 말은 쓰지 않는다
- 한 단계씩 끝내고, 단계마다 **동작 확인 방법**을 알려준 뒤 확인받고 다음 단계로 간다
- 코드 주석은 중요한 부분에만 단다
- 설명은 쉬운 말로, 짧게
