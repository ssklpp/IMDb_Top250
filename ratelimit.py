"""레이트리밋 키(누구의 요청으로 셀지)를 정한다. 외부 의존이 없는 순수 함수라 tests/unit에서 검증한다.

TCP로 붙은 상대(request.client.host)로 세면 안 된다. 배포 서버 앞에는 Railway 프록시가 있고, 그 주소가
요청마다 달라서 같은 PC에서 15개를 동시에 보내도 429가 한 번도 나지 않았다(docs/bug-log.md B-009).
로컬에서는 같은 코드가 11번째부터 정확히 429를 냈다.

판단 순서:
1. Vercel 프록시(web/app/api/chat/route.ts)가 보낸 방문자 IP — 공유 비밀값이 맞을 때만 믿는다.
   비밀값 없이 X-Client-IP를 믿으면 누구나 값을 바꿔 가며 제한을 피한다.
2. X-Forwarded-For의 맨 오른쪽 값 — 우리 쪽 프록시(Railway)가 붙인 실제 접속 주소다. 왼쪽 값은
   클라이언트가 보낸 그대로라 위조할 수 있다.
3. TCP 접속 주소 — 프록시가 없는 로컬 실행.
"""

import hmac
import ipaddress
from collections.abc import Mapping


def _valid_ip(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def rate_limit_key(headers: Mapping, peer: str | None, proxy_secret: str | None) -> tuple[str, str]:
    """(키, 출처)를 준다. 출처는 "proxy" / "forwarded" / "peer" 중 하나로 로그에만 쓴다.

    headers는 소문자 키로 찾는다(Starlette Headers는 대소문자를 가리지 않는다).
    """
    if proxy_secret:
        sent = headers.get("x-proxy-secret")
        if isinstance(sent, str) and hmac.compare_digest(sent.encode(), proxy_secret.encode()):
            ip = _valid_ip(headers.get("x-client-ip"))
            if ip:
                return ip, "proxy"

    forwarded = headers.get("x-forwarded-for")
    if isinstance(forwarded, str) and forwarded.strip():
        ip = _valid_ip(forwarded.split(",")[-1])
        if ip:
            return ip, "forwarded"

    return (peer or "unknown"), "peer"
