---
name: release
description: 이 저장소(IMDB 영화 챗봇)의 변경을 검증한 뒤 커밋·푸시하고 GitHub CI, Vercel·Railway 배포, 백엔드 /health까지 확인한다. 사용자가 "커밋", "푸시", "push", "커밋 메시지 달아서 올려줘", "배포해줘"라고 할 때 사용한다.
---

# release — 검증 → 커밋 → 푸시 → 배포 확인

이 저장소는 main 브랜치에 직접 푸시하고, 푸시하면 Vercel(프론트)과 Railway(백엔드)가
자동 배포한다. **CI와 배포는 연결돼 있지 않다** — CI가 실패해도 배포는 진행되므로
푸시 전 검증이 곧 배포 전 검증이다.

## 1. 변경 파악

```bash
git status --short
git diff --stat
```

- 커밋할 파일 목록을 정한다. 이번 작업과 무관한 변경이 섞여 있으면 **사용자에게 묻는다**.
- 작업 트리가 깨끗하면 커밋할 것이 없다고 알리고 멈춘다.

## 2. 검증 — 싼 것부터, 실패하면 멈춘다

| 조건 | 명령 | 비고 |
|---|---|---|
| 항상 | `.venv/Scripts/python -m pytest tests/unit/ -q` | 0.1초, 비용 0 |
| 항상 | `.venv/Scripts/python -m compileall -q agent.py server.py logging_config.py imdb_rag.py kobis_format.py sources.py scripts tests` | agent/server는 import에 키가 필요해 구문만 |
| 항상 | `.venv/Scripts/python scripts/check_docs.py` | 문서 숫자·로그 이벤트·제어 문자·참조 경로 |
| `web/` 변경 | `cd web && npm run lint && npm run build` | 빌드에 TypeScript 검사 포함 |
| `requirements.in` 변경 | `.venv/Scripts/python scripts/gen_lock.py` | `requirements.txt`를 **함께** 스테이지 |
| 도구·프롬프트·모델 변경 | 에이전트 평가 | **과금된다.** 이미 돌렸는지 확인하고, 안 돌렸으면 사용자에게 묻는다. 돌리지 않고 커밋한다면 커밋 메시지에 명시 |

- `check_docs.py`가 실패하면 문서의 숫자를 고친다. **과거를 설명하는 문장(~였습니다)은 그대로 둔다.**
- 어떤 평가를 돌릴지는 CLAUDE.md의 "같이 고쳐야 하는 쌍"과 "작업 순서"를 따른다.

## 3. 스테이지

```bash
git add <파일1> <파일2> ...
git diff --cached --stat
```

- **파일을 지정한다.** `git add .`나 `git add -A`는 쓰지 않는다 (`.env`·임시 파일 혼입 방지).
- `requirements.in`과 `requirements.txt`는 항상 함께.
- 코드와 그 변경을 설명하는 문서는 **같은 커밋에**.
- diff가 예상보다 크면 포맷이 통째로 바뀌지 않았는지 본다. JSON을 `json.dumps(indent=2)`로 다시 쓰면
  배열이 펼쳐져 diff가 수백 줄이 된다 — 원래 형식을 유지하는 텍스트 편집으로 고칠 것.

## 4. 커밋 메시지

```bash
git commit -q -F - <<'EOF'
<종류>: <한 줄 요약>

<배경 — 무엇이 문제였나, 어떻게 발견했나>

## 변경
- ...

## 검증
- 단위 테스트 N개 통과
- (돌린 평가와 결과)
- (돌리지 않은 검사가 있으면 그 사실과 이유)

<세션이 지정한 attribution 줄>
EOF
```

| 종류 | 언제 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `refactor` | 동작은 같고 구조 개선 |
| `ci` | GitHub Actions |
| `docs` | 문서만 |

한국어로 쓴다. "무엇을"보다 **"왜"** 를 남긴다 — 나중에 이력을 보는 사람이 같은 실수를 반복하지 않게.

## 5. 푸시

```bash
git push origin main
```

`--force`, `reset --hard`는 쓰지 않는다. 되돌릴 때는 `git revert <해시>` 후 푸시.

## 6. 배포 확인

```bash
.venv/Scripts/python .claude/skills/release/check_deploy.py
```

CI 3개 잡 완료 → Vercel·Railway 배포 기록 → Railway 배포 완료 → `/health`를 순서대로 확인한다.
보통 2~3분 걸린다.

| 종료 코드 | 의미 | 할 일 |
|---|---|---|
| 0 | 전부 정상 | 사용자에게 보고 |
| 1 | CI 실패, 배포 실패, 또는 /health 이상 | 실패 내용을 보고. 이미 배포된 경우 되돌릴지 사용자에게 묻는다 |
| 2 | 배포 기록 없음 등 경고 | Railway 대시보드 설정 확인을 안내 |

## 이 저장소에서 실제로 겪은 함정

- **`git checkout <파일>`은 HEAD가 아니라 스테이지에서 복원한다.** 스테이지한 뒤 되돌리려면
  `git checkout HEAD -- <파일>`. 이걸 몰라서 골든셋 항목이 중복된 적이 있다.
- **Bash 명령에 백슬래시 이스케이프가 든 텍스트를 쓰면 전달 과정에서 한 번 더 풀린다.**
  센티넬 표기를 텍스트로 쓰려다 실제 제어 문자(U+001F)가 커밋된 적이 있다.
  백슬래시가 필요한 텍스트는 Python에서 `chr(92)`로 조립하고, 커밋 전 `check_docs.py`로 확인한다.
- **`grep -P`는 Git Bash에서 제어 문자를 잡지 못한다.** "없음"이 나와도 믿지 말고 Python으로 검사한다.
- **Windows에서 Python 하위 프로세스 출력은 cp949다.** `python -X utf8`을 쓰거나 바이트로 받아 디코딩한다.
- **Railway 배포 기록이 없으면 Watch Paths부터 의심한다.** 볼륨 경로(`/app/vectorstore`)를 넣어
  모든 푸시가 조용히 건너뛰어진 적이 있다.
- **Railway는 빌드가 실패해도 이전 버전을 계속 서비스한다.** 사이트가 열린다고 새 버전이 아니다.
- **GitHub API는 로그인 없이 시간당 60회 제한이 있다.** 짧은 간격으로 반복 조회하지 않는다.
