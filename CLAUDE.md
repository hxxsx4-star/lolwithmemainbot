# 롤 같이 하자 · 메인봇

한국어 LoL 디스코드 서버 **「롤 같이 하자」**의 메인 봇. 로그 전용 봇은
`lolwithmelogbot` 저장소에 따로 있다.

담당 기능: 포인트 경제 · 레벨 · 경고 · 상점 · 프로필 카드 · 내전 · 문의함 ·
자동 인증(온보딩) · 이모지 역할 · 프로 경기 승부예측 · 매일 백업.

## 개발 환경

```bash
cd ~/lolwithmemainbot
source .venv/bin/activate     # Debian 13 은 PEP 668 이라 venv 밖에서 pip 설치가 막힌다
pip install -r requirements.txt
python bot.py
```

- Python 3.11 · discord.py 2.7.1 · aiosqlite · Pillow · aiohttp
- 비밀값은 `.env` 에 있고 커밋하지 않는다 (`DISCORD_TOKEN`, `RIOT_API_KEY`, `GUILD_ID`)
- 데이터는 `data/lolwithme.sqlite3` 하나에 모인다. 이것도 커밋하지 않는다

## 운영

봇은 VM 에서 tmux 세션으로 돈다.

```bash
tmux attach -t bot      # 붙기 (빠져나올 땐 Ctrl+B, D)
tmux kill-session -t bot && tmux new -s bot -d 'cd ~/lolwithmemainbot && .venv/bin/python bot.py'
```

배포는 `git pull` 후 재시작이다. 마이그레이션 스크립트는 없고, `core/db.py` 의
`_migrate()` 가 뜰 때 빠진 컬럼을 `ALTER TABLE` 로 채운다.

## 코드 규칙

- **주석과 사용자 문구는 한국어로 쓴다.** 코드 식별자는 영어.
- 주석은 "무엇을" 이 아니라 **"왜"** 를 적는다. 특히 겉보기에 이상한 선택
  (레이트 리밋 회피, 디스코드 제약 우회 등)에는 이유를 남긴다.
- **채널 ID · 역할 ID · 수치는 전부 `config.py` 에 모은다.** 코그에 상수를
  박지 않는다. 새 기능을 넣을 때도 같은 자리에 넣는다.
- 슬래시 명령어 이름과 인자는 한국어를 쓴다 (`/경고 [유저] [횟수] [사유]`).
- 사용자에게 보이는 실패는 **무엇이 왜 잘못됐는지** 알려 준다. 조용히 넘어가지
  않는다. 특히 온보딩 양식 파서는 틀린 곳을 전부 모아 한 번에 알려 준다.

### 자주 밟는 함정

- **디스코드 선택 메뉴는 25개까지**다. 챔피언 60종을 라인으로 나눈 이유다.
- **임베드 필드는 1024자까지**다. 목록을 잇기 전에 `join_names()` 같은 걸 쓴다.
- **역할은 봇 역할보다 아래에 있어야** 지급·수정이 된다. 안 되면 그렇다고 알린다.
- 지속 뷰(persistent view)는 `custom_id` 를 고정하고, 대상은 메시지 ID 로 DB 에서
  찾는다 (`cogs/scrim.py`, `cogs/prediction.py` 참고).
- 포인트를 여러 명에게 줄 때 로그 채널에 1인 1건씩 보내면 도배된다. 요약 한 건만 보낸다.

## 구조

```
bot.py          진입점 · 인텐트 · 확장 로드 · 명령어 동기화
config.py       채널/역할 ID, 경제·레벨·상점·승부예측 수치, 팔레트, 챔피언·LCK 목록
core/db.py      SQLite 저장소 (스키마 · 마이그레이션 · 모든 쿼리)
core/checks.py  슬래시 명령어 권한 검사
cogs/           기능별 코그
utils/          카드 렌더링 · 라이엇 API · 이스포츠 일정 · 양식 파서 · 역할 · 로그
```

## 확인 방법

테스트 프레임워크는 없다. 고친 뒤 최소한 이 둘은 돌린다.

```bash
python -m pyflakes cogs/*.py utils/*.py core/*.py bot.py config.py
python -c "
import asyncio, discord; from discord.ext import commands; import bot as b
async def m():
    x=commands.Bot(command_prefix='!',intents=discord.Intents.default()); x.db=None
    for e in b.EXTENSIONS: await x.load_extension(e)
    print(len(x.tree.get_commands()),'commands')
asyncio.run(m())"
```

디스코드나 외부 API 를 타는 로직은 **가짜 객체로 흐름을 돌려 확인한다.**
DB 가 필요하면 `DATA_DIR=/tmp/어딘가` 를 주고 진짜 SQLite 로 돌리면 된다.
프로필 카드는 `utils/card.py` 를 직접 호출해 PNG 를 뽑아 눈으로 본다.

## 하지 말 것

- `.env`, `data/`, 배경 이미지 등 `.gitignore` 에 있는 것을 커밋하지 않는다
- 사용자 데이터를 지우는 마이그레이션을 임의로 돌리지 않는다
- 포인트 지급·차감 로직을 바꿀 때는 **잠수/도배로 무한히 벌 수 있는 구멍이
  생기는지** 먼저 따져 본다
