# 제작법 1단계 — PC 준비 + 수집 테스트

## 이번 단계 목표
봇이 돌 PC에서 인스타·유튜브·웹 링크를 **실제로 받아올 수 있는지** 확인한다.
봇, AI, 옵시디언은 아직 없다. 이 단계가 안 되면 설계를 바꿔야 하니 가장 먼저 한다.

## 준비물
- 윈도우 PC (지금 쓰는 PC로 시작, 나중에 미니 PC로 폴더째 이전)
- 인스타 **부계정** (아이디·비밀번호, 폰 인스타 앱에 로그인되어 있으면 좋음)
- 테스트용 링크 4개: 유튜브 영상 1, 인스타 게시물(캐러셀) 1, 인스타 릴스 1, 웹 기사 1

모든 명령은 **PowerShell**에서 실행한다.

---

## 1. 기본 도구 설치

이미 있는 것: Python 3.12, ffmpeg, git. 새로 필요한 것은 **Deno**(yt-dlp가 유튜브를 처리할 때 씀) 하나다.

```powershell
winget install DenoLand.Deno
```
설치 후 **PowerShell 창을 닫고 새로 연다.**

프로젝트 폴더에서 가상환경 만들기:
```powershell
cd C:\02Workspaces\linkbot
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install "yt-dlp[default]" instaloader trafilatura python-dotenv
```

`Activate.ps1`이 "스크립트를 실행할 수 없다"며 막히면 한 번만:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

> ✅ **확인 지점 1**
> ```powershell
> ffmpeg -version
> deno --version
> yt-dlp --version
> instaloader --version
> ```
> 네 개 모두 버전이 나오면 통과.

---

## 2. 환경 파일 만들기

`.env.example`을 복사해서 `.env`를 만든다:
```powershell
Copy-Item .env.example .env
```
`.env`를 메모장으로 열어 `IG_USER=` 뒤에 **부계정 아이디**를 적는다. (비밀번호는 여기에 적지 않는다.)

---

## 3. 인스타 부계정 로그인 (한 번만)

```powershell
instaloader --login 부계정아이디
```
비밀번호는 **직접 입력**한다. 로그인 정보가 세션 파일로 저장되고, 다음부터는 이 파일을 쓴다.

**자주 생기는 일**
- "Checkpoint required" 또는 본인 확인 요구 → 폰 인스타 앱에서 "본인이 맞습니다"를 누르고 다시 시도
- 로그인이 계속 막히면 30분~1시간 기다렸다가 재시도. 연속으로 여러 번 시도하면 더 막힌다
- 그래도 안 되면 알려주기. 브라우저에 로그인된 세션을 가져오는 방법이 있다

> ✅ **확인 지점 2**
> "Saved session to ..." 메시지가 나오면 통과.

---

## 4. 실행

`test_collect.py`는 이미 만들어져 있다. 링크는 반드시 따옴표로 감싼다 (`?`, `&` 때문에).

먼저 한글이 깨지지 않게 (창을 새로 열 때마다):
```powershell
$env:PYTHONUTF8 = "1"
```

```powershell
python test_collect.py yt "유튜브링크"
python test_collect.py ig "인스타게시물링크"
python test_collect.py ig "인스타릴스링크"
python test_collect.py web "웹기사링크"
```

> ✅ **확인 지점 3 — 이번 단계 통과 기준**
> | 테스트 | 통과 조건 |
> |---|---|
> | 유튜브 | 제목이 나오고, 자막 파일이 1개 이상 (자막 없는 영상이면 제목만 나와도 통과) |
> | 인스타 게시물 | 받은 파일 합계(이미지+영상)가 장수와 같음. 캐러셀에는 영상이 섞일 수 있음 |
> | 인스타 릴스 | 영상 파일(.mp4)이 1개 이상 |
> | 웹 | 본문 길이가 수백 자 이상 |

---

## 5. 막혔을 때

| 증상 | 의미 | 대응 |
|---|---|---|
| 유튜브 "Sign in to confirm you're not a bot" | IP를 봇으로 의심 | 결과 기록. 브라우저 쿠키 사용 방법을 같이 정한다 |
| 유튜브 "n challenge" 또는 JS runtime 경고 | Deno를 못 찾음 | 새 PowerShell 창에서 `deno --version` 확인 |
| 인스타 401 / 403 | 로그인 정보 문제 | 3번 로그인 다시 |
| 인스타 429 / "Please wait a few minutes" | 요청이 너무 많음 | 30분 이상 쉬고 1개만 다시 |
| 웹 본문 추출 실패 | 사이트가 막거나 구조가 특이함 | 다른 사이트로 재시도. 일부 실패는 정상 |

**주의**: 인스타는 테스트라고 여러 번 연속으로 돌리지 말 것. 링크당 1~2회면 충분하다.

---

## 6. 결과 보고

아래 표를 채워서 보내주면 2단계 확인 후 3단계로 넘어간다.

| 테스트 | 성공/실패 | 실패 시 오류 메시지 |
|---|---|---|
| 유튜브 | | |
| 인스타 게시물 | | |
| 인스타 릴스 | | |
| 웹 기사 | | |

전부 성공이면 설계 그대로 진행.
인스타나 유튜브가 막히면 그 결과에 맞춰 수집 방식을 조정한 뒤 진행한다.
