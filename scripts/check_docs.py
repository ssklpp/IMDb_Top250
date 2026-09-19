"""문서에 적힌 숫자·목록이 코드와 일치하는지 검사한다. 어긋나면 종료 코드 1.

문서가 코드보다 뒤처지는 일이 실제로 반복됐다. 단위 테스트 개수가 60 → 66 → 78로
바뀌는 동안 문서는 두 번 옛 숫자에 머물렀고, 로그 이벤트 목록은 처음부터 6개가
빠져 있었고, CLAUDE.md에는 제어 문자(U+001F)가 커밋된 채 남아 있었다.
사람이 기억해서 맞추는 방식으로는 막을 수 없어 CI에서 검사한다.

사용법 (저장소 어디서 실행해도 된다):
    python scripts/check_docs.py

표준 라이브러리와 pytest(테스트 개수 세기)만 쓴다. CI의 python 잡에 pytest만
설치돼 있어도 돌아가야 하기 때문이다.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 과거를 설명하는 문장은 검사하지 않는다. 예: README의
# "requirements.txt가 직접 의존성 13개만 고정하고 ... 열어둔 상태였습니다"
HISTORICAL = re.compile(r"였습니다|였다|당시|이전에는")

# 로그 이벤트로 인정하는 접두어. 문서의 다른 백틱 표기(`kobis_format.py` 등)와 구분한다.
EVENT_PREFIXES = ("chat", "cli", "health", "kobis", "shutdown", "startup", "vectorstore", "web")

TEXT_SUFFIXES = (".py", ".md", ".ts", ".tsx", ".json", ".yml", ".yaml", ".toml", ".txt", ".in", ".css")


def git_files(*patterns: str) -> list[str]:
    """추적 중인 파일 + 아직 add하지 않은 새 파일 (.gitignore 대상은 제외).

    --others가 없으면 새로 만든 파일은 커밋된 뒤에야 검사된다. 실제로 이 스크립트를
    처음 돌렸을 때 새 파일 5개가 제어 문자 검사에서 빠져 있었다.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", *patterns],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return [f for f in out.split("\n") if f and not f.startswith("web/node_modules/")]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# ── 실제 값 ─────────────────────────────────────────────────────────────


def unit_test_count() -> int:
    # Windows에서는 하위 프로세스 출력이 cp949라 바이트로 받아 필요한 숫자만 뽑는다.
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit/", "-q", "--collect-only"],
        cwd=ROOT,
        capture_output=True,
    ).stdout.decode("utf-8", errors="replace")
    m = re.search(r"(\d+) tests? collected", out)
    if not m:
        raise SystemExit(f"pytest 수집 결과를 읽지 못했습니다:\n{out[-500:]}")
    return int(m.group(1))


def eval_count() -> int:
    return len(json.loads(read("tests/evals/golden_dataset.json")))


def requirement_count(path: str) -> int:
    return sum(1 for line in read(path).splitlines() if line.strip() and not line.strip().startswith("#"))


def ci_import_count() -> int:
    """ci.yml deps 잡이 import해 보는 서드파티 모듈 수."""
    text = read(".github/workflows/ci.yml")
    block = text[text.index("targets = [") : text.index("]", text.index("targets = ["))]
    return len(re.findall(r'^\s*\("', block, flags=re.M))


def log_events_in_code() -> set[str]:
    code = "".join(read(f) for f in ("agent.py", "server.py", "imdb_rag.py"))
    # 이벤트 이름이 다음 줄에 오는 경우(log.info(\n    "kobis.request", ...))도 잡도록 \s*
    names = re.findall(r'log\.(?:info|warning|exception|error)\(\s*"([a-z_]+\.[a-z_]+)"', code)
    return {n for n in names if n.split(".")[0] in EVENT_PREFIXES}


def log_events_in_doc() -> set[str]:
    names = re.findall(r"`([a-z_]+\.[a-z_]+)`", read("rules/server.md"))
    return {n for n in names if n.split(".")[0] in EVENT_PREFIXES and not n.endswith((".py", ".md"))}


# ── 검사 ────────────────────────────────────────────────────────────────


def check_counts(problems: list[str]) -> None:
    actual = {
        "단위 테스트": unit_test_count(),
        "평가 항목": eval_count(),
        "직접 의존성": requirement_count("requirements.in"),
        "의존성 락": requirement_count("requirements.txt"),
        "CI import 확인": ci_import_count(),
    }
    rules = [
        ("단위 테스트", r"단위 테스트 (\d+)개"),
        ("평가 항목", r"평가 (\d+)개"),
        ("평가 항목", r"평가 \**(\d+)/\d+"),
        ("평가 항목", r"평가 \**\d+/(\d+)"),
        ("평가 항목", r"(?:골든셋|데이터셋) (\d+)개"),
        ("평가 항목", r"전체 (\d+)개를 돌린다"),
        ("직접 의존성", r"직접 의존성 (\d+)개"),
        ("의존성 락", r"의존성 락 (\d+)개"),
        ("의존성 락", r"락 (\d+)개"),
        ("의존성 락", r"의존성 (\d+)개(?: 전체 고정|를 고정)"),
        ("CI import 확인", r"서드파티 (\d+)개"),
    ]
    docs = git_files("*.md", ".github/workflows/*.yml")
    for path in docs:
        for lineno, line in enumerate(read(path).splitlines(), 1):
            if HISTORICAL.search(line):
                continue
            for label, pattern in rules:
                for found in re.findall(pattern, line):
                    if int(found) != actual[label]:
                        problems.append(f"{path}:{lineno}: {label} — 문서 {found}, 실제 {actual[label]}")
    print("실제 값:", ", ".join(f"{k} {v}" for k, v in actual.items()))


def check_log_events(problems: list[str]) -> None:
    code, doc = log_events_in_code(), log_events_in_doc()
    for name in sorted(code - doc):
        problems.append(f"rules/server.md: 로그 이벤트 `{name}`이 코드에는 있는데 문서에 없음")
    for name in sorted(doc - code):
        problems.append(f"rules/server.md: 로그 이벤트 `{name}`이 문서에는 있는데 코드에 없음")
    print(f"로그 이벤트: 코드 {len(code)}개, 문서 {len(doc)}개")


def check_control_chars(problems: list[str]) -> None:
    """grep -P는 Git Bash에서 제어 문자를 못 잡는다. Python으로 직접 본다."""
    count = 0
    for path in git_files():
        if not path.endswith(TEXT_SUFFIXES):
            continue
        count += 1
        for lineno, line in enumerate(read(path).split("\n"), 1):
            bad = [c for c in line if ord(c) < 32 and c not in "\t\r"]
            if bad:
                codes = ", ".join(sorted({f"U+{ord(c):04X}" for c in bad}))
                problems.append(f"{path}:{lineno}: 제어 문자 {codes}")
    print(f"제어 문자 검사: 텍스트 파일 {count}개")


def check_references(problems: list[str]) -> None:
    """CLAUDE.md가 가리키는 문서·스크립트 경로가 실제로 있는지."""
    refs = set(re.findall(r"`((?:rules|tests|web|scripts|\.claude)/[\w./-]+\.(?:md|py))`", read("CLAUDE.md")))
    refs |= {"README.md"}
    for ref in sorted(refs):
        if not (ROOT / ref).exists():
            problems.append(f"CLAUDE.md: `{ref}`를 가리키지만 파일이 없음")
    print(f"CLAUDE.md 참조 경로: {len(refs)}개")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    problems: list[str] = []
    check_counts(problems)
    check_log_events(problems)
    check_control_chars(problems)
    check_references(problems)

    if problems:
        print(f"\n문서와 코드가 어긋난 곳 {len(problems)}건:")
        for p in problems:
            print("  -", p)
        print("\n문서의 숫자를 고칠 때 과거를 설명하는 문장(~였습니다)은 그대로 둘 것.")
        return 1
    print("\n모두 일치합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
