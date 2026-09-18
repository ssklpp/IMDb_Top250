# CI · 의존성 · 배포 규칙

> 워크플로, 패키지, 배포 설정을 고칠 때 먼저 읽을 것. 개요는 `CLAUDE.md`.

## CI (`.github/workflows/`)
> 잡 구성 표는 `CLAUDE.md`에 있다. 여기에는 그렇게 나눈 이유를 적는다.

## `deps` 잡이 막는 것
Railway 배포 실패를 **푸시 시점에** 잡는다. 과거에 배포본이 7커밋 뒤처진 채로 돌고 있었는데,
빌드가 실패해도 이전 Active 배포가 유지되어 겉으로는 정상으로 보였다.

- `ubuntu-latest` + `.python-version`(3.13) — **nixpacks가 쓰는 것과 같은 조건**
- `pip install -r requirements.txt` — Railway가 실행하는 것과 같은 명령
- 서드파티 19개를 실제로 import — 설치 성공과 사용 가능은 다르다.
  특히 `AsyncSqliteSaver`는 `langgraph-checkpoint-sqlite`라는 **별도 패키지**라 빠지기 쉽다.
- `requirements.in`과 락의 드리프트 검사 — `.in`에 추가하고 `gen_lock.py`를 잊는 실수를 막는다

## 두 워크플로를 나눈 기준은 비용
`ci.yml`은 **외부 API를 전혀 호출하지 않아** 시크릿 없이 돌고 포크 PR에서도 안전하다.
`evals.yml`만 실제 LLM을 부르므로 수동 실행이다.

`python` 잡이 수십 초에 끝나는 건 단위 테스트 대상(`kobis_format.py`, `sources.py`)이
표준 라이브러리만 쓰도록 분리돼 있어 **pytest만 설치하면 되기 때문**이다.
반면 `deps` 잡은 락 78개를 전부 설치하므로 몇 분 걸린다 — 그래서 잡을 나눴다.
병렬로 돌아 전체 소요 시간은 크게 늘지 않는다.

`agent.py`/`server.py`는 import만 해도 벡터스토어 빌드와 API 키를 요구해 CI에서 실행할 수
없다. `python` 잡은 `compileall`로 구문 오류만 잡고, `deps` 잡은 서드파티를 직접 import해
패키지가 실제로 쓸 수 있는 상태인지 확인한다.

주의사항:
- **`.python-version`을 `setup-python`이 읽는다.** 로컬·Railway·CI가 같은 파일 하나를 본다.
- `evals.yml`은 `vectorstore`를 **PDF 해시로 캐시**한다. 청크 파라미터(`chunk_size` 등)를
  바꾸면 캐시가 낡으므로 캐시 키의 `vectorstore-v1`을 `v2`로 올릴 것.
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
