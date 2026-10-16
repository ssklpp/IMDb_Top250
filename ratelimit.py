"""레이트리밋 키(누구의 요청으로 셀지)와 프록시 경유 여부를 판단한다. 외부 의존이 없는 순수 함수라
tests/unit에서 검증한다.

TCP로 붙은 상대(request.client.host)로 세면 안 된다. 배포 서버 앞에는 Railway 프록시가 있고, 그 주소가
요청마다 달라서 같은 PC에서 15개를 동시에 보내도 429가 한 번도 나지 않았다(docs/bug-log.md B-009).
로컬에서는 같은 코드가 11번째부터 정확히 429를 냈다.

X-Forwarded-For도 쓰지 않는다. 맨 오른쪽 값을 "우리 프록시가 붙인 접속 주소"로 보고 배포했는데, Railway에서는
그 값도 요청마다 달라 제한이 걸리지 않았다(실측). 왼쪽 값은 클라이언트가 위조할 수 있다.

그래서 방문자 IP는 우리 프록시(web/app/api/chat/route.ts)가 공유 비밀값과 함께 보낸 것만 믿는다.
비밀값 없이 X-Client-IP를 믿으면 누구나 값을 바꿔 가며 제한을 피한다. 비밀값이 설정된 서버는 프록시를
거치지 않은 요청을 아예 받지 않는다(server.py) — 그런 요청은 방문자를 구분할 방법이 없다.
"""

import hmac
import ipaddress
from collections.abc import Mapping


def _valid_ip(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def from_proxy(headers: Mapping, proxy_secret: str | None) -> bool:
    """우리 프록시가 보낸 요청인지. 서버에 비밀값이 없으면 항상 False(빈 값끼리 일치로 통과하지 않게)."""
    if not proxy_secret:
        return False
    sent = headers.get("x-proxy-secret")
    return isinstance(sent, str) and hmac.compare_digest(sent.encode(), proxy_secret.encode())


def rate_limit_key(headers: Mapping, peer: str | None, proxy_secret: str | None) -> tuple[str, str]:
    """(키, 출처)를 준다. 출처는 "proxy" / "peer"로 로그에만 쓴다.

    headers는 소문자 키로 찾는다(Starlette Headers는 대소문자를 가리지 않는다).
    """
    if from_proxy(headers, proxy_secret):
        ip = _valid_ip(headers.get("x-client-ip"))
        if ip:
            return ip, "proxy"
    return (peer or "unknown"), "peer"
