"""requirements.in의 의존성 트리만 추적해 requirements.txt(락)를 생성한다.

`pip freeze`를 쓰지 않는 이유: venv에는 개발 도구(pytest 등 requirements-dev.txt)도
함께 설치돼 있어서 freeze를 그대로 쓰면 Railway가 설치할 프로덕션 락에
개발 의존성이 섞여 들어간다. 여기서는 requirements.in에 적힌 직접 의존성에서
출발해 실제로 도달 가능한 패키지만 모은다.

사용법 (반드시 venv 안에서):
    .venv/Scripts/python scripts/gen_lock.py
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, requires, version
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent
IN_PATH = ROOT / "requirements.in"
OUT_PATH = ROOT / "requirements.txt"

HEADER = """# 이 파일은 scripts/gen_lock.py가 생성한 의존성 락입니다. 직접 편집하지 마세요.
# 직접 의존성은 requirements.in에서 관리합니다. 재생성 방법은 CLAUDE.md의 "의존성 관리" 참고.
# Python 3.13 (.python-version) 기준이며 Railway/nixpacks가 이 파일로 설치합니다.
"""


def direct_requirements() -> list[str]:
    names = []
    for line in IN_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(Requirement(line).name)
    return names


def resolve_closure(roots: list[str]) -> dict[str, str]:
    """설치된 메타데이터를 따라가며 도달 가능한 패키지와 그 버전을 모은다."""
    found: dict[str, str] = {}
    stack = list(roots)
    while stack:
        name = stack.pop()
        key = name.lower().replace("_", "-")
        if key in found:
            continue
        try:
            found[key] = version(name)
        except PackageNotFoundError:
            print(f"  경고: '{name}'이 설치되어 있지 않아 건너뜁니다", file=sys.stderr)
            continue
        for raw in requires(name) or []:
            try:
                req = Requirement(raw)
            except Exception:
                continue
            # extras 전용/환경 한정 의존성은 현재 환경 기준으로 판단한다
            if req.marker and not req.marker.evaluate():
                continue
            stack.append(req.name)
    return found


def main() -> int:
    roots = direct_requirements()
    closure = resolve_closure(roots)

    body = "\n".join(f"{name}=={ver}" for name, ver in sorted(closure.items()))
    OUT_PATH.write_text(f"{HEADER}\n{body}\n", encoding="utf-8", newline="\n")

    print(f"직접 의존성 {len(roots)}개 → 락 {len(closure)}개를 {OUT_PATH.name}에 기록했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
