# CI · 의존성 · 배포 규칙

> 워크플로, 패키지, 배포 설정을 고칠 때 먼저 읽을 것. 개요는 `CLAUDE.md`.

## CI (`.github/workflows/`)
> 잡 구성 표는 `CLAUDE.md`에 있다. 여기에는 그렇게 나눈 이유를 적는다.

## 문서-코드 대조 (`python` 잡)
`scripts/check_docs.py`가 다음을 코드와 대조하고, 어긋나면 파일과 줄 번호를 찍고 실패한다.

- 문서의 개수 표기 — 단위 테스트·평가 항목·직접 의존성·락·CI import 확인 수
- `rules/server.md`의 로그 이벤트 목록 ↔ 코드의 `log.*("이벤트명")` (양방향)
- 추적되는 텍스트 파일의 제어 문자
- CLAUDE.md가 가리키는 문서·스크립트 경로의 존재

문서가 코드보다 뒤처지는 일이 실제로 반복됐다(테스트 개수 60 → 66 → 78 동안 두 번 옛 숫자,
로그 이벤트 6개 누락, 제어 문자 커밋). `grep -P`는 Git Bash에서 제어 문자를 못 잡아서 Python으로 본다.
과거를 설명하는 문장(`였습니다`·`였다`·`당시`·`이전에는`이 든 줄)은 검사하지 않는다.
표준 라이브러리와 pytest만 쓰므로 `python` 잡의 설치 구성 그대로 돈다.

## `deps` 잡이 막는 것
Railway 배포 실패를 **푸시 시점에** 잡는다. 과거에 배포본이 7커밋 뒤처진 채로 돌고 있었는데,
빌드가 실패해도 이전 Active 배포가 유지되어 겉으로는 정상으로 보였다.

- `ubuntu-latest` + `.python-version`(3.13) — **nixpacks가 쓰는 것과 같은 조건**
- `pip install -r requirements.txt` — Railway가 실행하는 것과 같은 명령
- 서드파티 20개를 실제로 import — 설치 성공과 사용 가능은 다르다.
- `tests/mcp/` — MCP 서버를 메모리 안에서 띄워 도구 정의·입력 검증을 본다. mcp·langchain이 필요해서
  pytest만 까는 `python` 잡이 아니라 여기서 돈다. 외부 API는 부르지 않는다.
  특히 `AsyncSqliteSaver`는 `langgraph-checkpoint-sqlite`라는 **별도 패키지**라 빠지기 쉽다.
- `requirements.in`과 락의 드리프트 검사 — `.in`에 추가하고 `gen_lock.py`를 잊는 실수를 막는다

## `docker` 잡과 컨테이너 구성
`docker-compose.yml`은 **로컬 원클릭 실행용**이다(`web` → `api` → `mcp`). 배포는 여전히 nixpacks(Railway)와
Vercel이라, 이 파일이 깨져도 배포에는 티가 나지 않는다 — 그래서 CI에서 이미지 빌드만 확인한다.
실행은 하지 않는다(OpenAI 키와 임베딩이 필요해 비용이 든다).

- **브라우저는 `web`만 본다.** `route.ts`가 서버 쪽에서 `BACKEND_URL=http://api:8000`으로 프록시하므로
  api 포트를 열지 않고 CORS 설정도 필요 없다.
- **기동 순서는 헬스체크로 묶는다**: `mcp`(벡터스토어 준비) → `api`(MCP에서 도구 4개 받음) → `web`.
  처음 뜰 때는 `mcp`가 250편을 임베딩해야 200이라 `mcp`의 `start_period`를 길게 뒀다.
- **`mcp`와 `api`는 같은 이미지**를 명령만 바꿔 쓴다(`docker/api.Dockerfile`). 의존성이 같다.
- **키는 서비스마다 필요한 것만** `.env`에서 골라 넣는다(`env_file` 대신 `environment`). KOBIS·Tavily 키는
  `mcp`만, `api`는 OpenAI 키(LLM)만 갖는다. `mcp`도 검색어 임베딩에 OpenAI 키가 필요하다.
- **볼륨 둘**: `movie_index`(mcp의 FAISS 인덱스), `chat_db`(api의 `checkpoints.sqlite`). `down` 후 다시 올려도
  임베딩을 반복하지 않고 대화도 이어진다.
- **Railway는 MCP를 쓰지 않는다.** `MCP_URL`이 없으면 `server.py`가 로컬 도구를 쓴다. 배포 구성은 그대로다.
- **api 이미지는 2단계 빌드**: `requirements.txt`(락)만 먼저 복사해 설치 층을 캐시하고, 실행 이미지에는
  venv와 `*.py`·CSV·`movie_tools/`·`movie_mcp/`만 둔다. `docker/api.Dockerfile.dockerignore`가 `.env`를
  컨텍스트에서 뺀다 — 키는 compose `environment`로 실행 시에만 들어간다. **새 패키지 폴더를 만들면
  Dockerfile의 COPY에도 추가할 것**(안 하면 컨테이너에서만 ImportError). 비루트 사용자(`app`)로 돈다.
- **web 이미지는 `output: "standalone"`**(`next.config.ts`). standalone은 `public`·`.next/static`을
  복사하지 않으므로 Dockerfile에서 직접 복사한다. 빠뜨리면 페이지는 뜨는데 CSS·글꼴이 404가 된다.
- **저장소 루트에 `Dockerfile`·`.dockerignore`를 두지 말 것.** 처음엔 루트에 뒀다가 Railway 배포가
  실패했다(배포는 nixpacks인데 Railway가 루트의 Docker 파일을 집어 든다). 그래서 api용은
  `docker/api.Dockerfile`, 제외 목록은 BuildKit의 Dockerfile별 규칙으로 `docker/api.Dockerfile.dockerignore`에
  둔다. 빌드 컨텍스트는 여전히 루트다. `web/Dockerfile`은 Railway가 보지 않는 하위 폴더라 괜찮다.
- **Python 버전은 `.python-version`과 손으로 맞춘다.** Dockerfile의 `python:3.13-slim`은 그 파일을
  읽지 않는다. 버전을 올릴 때 함께 고칠 것.
- Postgres는 아직 없다. 체크포인터를 Postgres로 바꾸는 단계(`docs/roadmap.md` 3단계)에서 추가한다.

## 두 워크플로를 나눈 기준은 비용
`ci.yml`은 **외부 API를 전혀 호출하지 않아** 시크릿 없이 돌고 포크 PR에서도 안전하다.
`evals.yml`만 실제 LLM을 부르므로 수동 실행이다.

`python` 잡이 수십 초에 끝나는 건 단위 테스트 대상(`kobis_format.py`, `sources.py`)이
표준 라이브러리만 쓰도록 분리돼 있어 **pytest만 설치하면 되기 때문**이다.
반면 `deps` 잡은 락 85개를 전부 설치하므로 몇 분 걸린다 — 그래서 잡을 나눴다.
병렬로 돌아 전체 소요 시간은 크게 늘지 않는다.

`agent.py`/`server.py`는 import만 해도 API 키를 요구하고 에이전트를 만들면 벡터스토어를 빌드해 CI에서 실행할 수
없다. `python` 잡은 `compileall`로 구문 오류만 잡고, `deps` 잡은 서드파티를 직접 import해
패키지가 실제로 쓸 수 있는 상태인지 확인한다.

주의사항:
- **`.python-version`을 `setup-python`이 읽는다.** 로컬·Railway·CI가 같은 파일 하나를 본다.
- `evals.yml`은 `vectorstore`를 **`imdb_top250.csv` + `imdb_data.py` 해시로 캐시**한다. 둘 중
  하나가 바뀌면 키가 바뀌어 새로 만든다. `movie_tools/imdb.py`도 같은 내용의 지문으로 인덱스 파일 이름을
  정하므로 손으로 올릴 버전 번호는 없다.
- Railway 볼륨(`/app/vectorstore`)에는 PDF 시절의 `index.faiss`·`index.pkl`이 남아 있다. 더는
  읽히지 않으니 두어도 되고, 지운다면 **같은 폴더의 `checkpoints.sqlite*`는 지우지 말 것**(대화 기록).
- 평가를 자동 실행하려면 `evals.yml`의 주석 처리된 `push`/`schedule` 트리거를 풀면 되지만,
  **실행마다 과금된다.**

## 의존성 관리

**Python 버전과 패키지 버전이 로컬과 Railway에서 동일하도록 고정돼 있다.** 이 구조를 깨뜨리지 말 것.
> 파일별 역할 표는 `CLAUDE.md`에 있다.

### 패키지를 추가·변경할 때
1. `requirements.in`(프로덕션) 또는 `requirements-dev.txt`(개발 도구)를 수정한다.
2. venv에서 설치하고 락을 재생성한다:
   ```bash
   .venv/Scripts/python -m pip install -r requirements.in
   .venv/Scripts/python scripts/gen_lock.py
   ```
3. `pytest tests/unit/ -q`로 먼저 확인하고, 필요하면 `python -m tests.evals.run_evals --skip-judge`까지 돌린다.
4. `requirements.in`과 `requirements.txt`를 **함께** 커밋한다.

> **락의 플랫폼 조건은 Linux 기준이다.** `gen_lock.py`는 `sys_platform == "win32"` 같은 조건을 이 스크립트를
> 돌린 PC가 아니라 배포 환경(linux x86_64, `TARGET_ENV`)으로 판단한다. 예전엔 현재 PC 기준이라 Windows에서
> 락을 만들면 Windows 전용 패키지가 섞였다 — `mcp`를 추가했을 때 `pywin32==312`가 들어가 리눅스 설치가
> 깨질 뻔했다(`docs/bug-log.md` B-010). Windows 로컬에 필요한 `colorama`·`pywin32`는 pip가 알아서 깐다.

> **`pip freeze > requirements.txt`를 쓰지 말 것.** venv에는 개발 도구(pytest 등)도 설치돼
> 있어서 freeze를 그대로 쓰면 프로덕션 락에 개발 의존성이 섞여 Railway까지 실려간다.
> `scripts/gen_lock.py`는 `requirements.in`의 의존성 트리만 추적하므로 이 문제가 없다.

### 왜 이렇게 하는가
이전에는 `requirements.txt`가 직접 의존성만 고정하고 transitive를 열어둬서, 배포할 때마다 다른 버전이 깔렸다. 실측 당시 로컬은 `langchain-core 1.2.26`/`langsmith 0.4.38`이었지만 Railway는 `1.6.2`/`0.12.4`를 설치하고 있었고, Python도 로컬 3.14 대 Railway 3.11(nixpacks 기본값)로 갈려 있었다. 로컬 검증이 배포본을 보증하지 못하는 상태였다.

`pip freeze`를 전역 환경에서 돌리면 무관한 패키지가 섞이므로 **반드시 venv 안에서** 실행할 것.

## 배포 (Vercel + Railway)

### Railway (백엔드)
- `railway.toml`에 시작 명령 및 헬스체크 설정 포함
- 환경변수: `.env.example` 참고, `CORS_ORIGINS`는 Vercel 도메인으로 설정
- 볼륨 마운트: `/app/vectorstore` (vectorstore 영구 저장)
- 백엔드 URL: `https://imdbtop250-production.up.railway.app`

#### Railway 배포 주의사항
- **`requests`를 requirements.txt에 직접 명시하지 말 것**: `langchain-community`가 `requests>=2.32.5`를 요구하므로 버전을 고정하면 의존성 충돌이 발생한다. `requests`는 transitive dependency로 자동 설치된다.
- **빌드 실패 시 Active 배포는 유지됨**: 새 빌드가 실패해도 이전 버전이 계속 Active 상태로 서비스된다. Deployments 탭에서 각 배포의 커밋 해시를 확인해 실제로 어떤 버전이 빌드되었는지 확인할 것.
- **캐시로 인해 최신 커밋이 빌드되지 않을 경우**: Deployments 탭에서 실패한 배포를 Redeploy하면 그 커밋 기준으로 재시도한다. 최신 커밋으로 빌드하려면 GitHub push로 새 배포를 트리거해야 한다.

### Vercel (프론트엔드)
- Root Directory: `web`
- 환경변수: `BACKEND_URL` = Railway 백엔드 URL
- `web/.env.local`은 로컬 전용, Vercel에는 대시보드에서 직접 설정
