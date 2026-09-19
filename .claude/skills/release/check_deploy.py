"""푸시한 커밋의 CI 결과, Vercel·Railway 배포, 백엔드 /health를 확인한다.

사용법 (저장소 루트에서):
    python .claude/skills/release/check_deploy.py          # HEAD 커밋
    python .claude/skills/release/check_deploy.py 9b67933  # 특정 커밋

종료 코드: 0 = 전부 정상, 1 = 실패가 있음, 2 = 경고(배포 기록 없음 등)

표준 라이브러리만 쓴다. GitHub API는 로그인 없이 쓰면 시간당 60회 제한이라
20초 간격으로 폴링한다. 환경변수 GITHUB_TOKEN이 있으면 붙여서 제한을 올린다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "ssklpp/IMDb_Top250"
HEALTH_URL = "https://imdbtop250-production.up.railway.app/health"
EXPECTED_CHECKS = 3  # ci.yml의 python / deps / frontend
POLL_S = 20
TIMEOUT_S = 600


def api(path: str):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "release-check"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait(label: str, fn):
    """fn()이 None이 아닌 값을 줄 때까지 폴링한다."""
    start = time.monotonic()
    while True:
        result = fn()
        if result is not None:
            return result
        if time.monotonic() - start > TIMEOUT_S:
            return None
        print(f"  … {label} 대기 중 ({int(time.monotonic() - start)}초)", flush=True)
        time.sleep(POLL_S)


def check_ci(sha: str) -> bool:
    print("[1] GitHub Actions CI")

    def done():
        runs = api(f"commits/{sha}/check-runs")["check_runs"]
        runs = [r for r in runs if r["app"]["slug"] == "github-actions"]
        if len(runs) >= EXPECTED_CHECKS and all(r["status"] == "completed" for r in runs):
            return runs
        return None

    runs = wait("CI", done)
    if runs is None:
        print("  ✗ 시간 초과 — Actions 탭에서 직접 확인하세요")
        return False
    ok = True
    for r in runs:
        mark = "✓" if r["conclusion"] == "success" else "✗"
        ok &= r["conclusion"] == "success"
        print(f"  {mark} {r['name']} — {r['conclusion']}")
    return ok


def check_deployments(sha: str) -> int:
    """0 정상, 1 실패, 2 경고."""
    print("[2] 배포 기록")
    deps = api(f"deployments?sha={sha}")
    by_bot = {d["creator"]["login"]: d for d in deps}
    status = 0

    if "vercel[bot]" in by_bot:
        print("  ✓ Vercel 배포 생성됨")
    else:
        print("  ! Vercel 배포 기록 없음")
        status = 2

    railway = by_bot.get("railway-app[bot]")
    if railway is None:
        print("  ! Railway 배포 기록 없음 — 대시보드의 Watch Paths와 자동 배포 설정을 확인하세요.")
        print("    (과거에 Watch Paths에 볼륨 경로 /app/vectorstore를 넣어 모든 푸시가 건너뛰어졌다)")
        return 2

    def finished():
        states = api(f"deployments/{railway['id']}/statuses")
        if states and states[0]["state"] in ("success", "failure", "error", "inactive"):
            return states[0]["state"]
        return None

    state = wait("Railway 배포", finished)
    if state == "success":
        print("  ✓ Railway 배포 성공")
    elif state == "inactive":
        # 성공했다가 이후 커밋의 배포로 교체된 경우. 옛 커밋을 확인할 때 정상적으로 나온다.
        print("  ✓ Railway 배포됐다가 이후 커밋의 배포로 교체됨")
    else:
        print(f"  ✗ Railway 배포 상태: {state or '시간 초과'}")
        print("    빌드가 실패해도 Railway는 이전 버전을 계속 서비스한다. 사이트가 열린다고 새 버전이 아니다.")
        status = 1
    return status


def check_health() -> bool:
    print("[3] 백엔드 /health")
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            print(f"  ✓ HTTP {resp.status} — status={body.get('status')}, checks={body.get('checks')}")
            return body.get("status") == "ok"
    except urllib.error.HTTPError as e:
        print(f"  ✗ HTTP {e.code}")
    except Exception as e:  # 네트워크 실패도 여기서 보고만 한다
        print(f"  ✗ {type(e).__name__}")
    return False


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ref = sys.argv[1] if len(sys.argv) > 1 else "HEAD"
    sha = subprocess.run(["git", "rev-parse", ref], capture_output=True, text=True, check=True).stdout.strip()
    print(f"커밋 {sha[:7]}\n")

    try:
        ci_ok = check_ci(sha)
        print()
        deploy = check_deployments(sha)
        print()
        health_ok = check_health()
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print("\nGitHub API 요청 제한에 걸렸습니다. 한 시간 뒤 다시 하거나 GITHUB_TOKEN을 설정하세요.")
        raise

    print()
    if not ci_ok or deploy == 1 or not health_ok:
        print("결과: 실패 항목이 있습니다.")
        return 1
    if deploy == 2:
        print("결과: 경고가 있습니다.")
        return 2
    print("결과: CI · 배포 · 헬스체크 모두 정상")
    return 0


if __name__ == "__main__":
    sys.exit(main())
