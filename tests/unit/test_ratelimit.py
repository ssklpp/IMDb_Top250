"""ratelimit.rate_limit_key 단위 테스트.

배포 서버에서 레이트리밋이 전혀 걸리지 않았다(B-009). TCP 접속 주소가 Railway 프록시라 요청마다 달랐다.
여기서는 키를 무엇으로 고르는지와, 그 선택을 클라이언트가 위조로 피할 수 없는지를 고정한다.
"""

from ratelimit import rate_limit_key

SECRET = "s3cret-value"


def test_비밀값이_맞으면_프록시가_보낸_방문자_IP로_센다():
    """Vercel 경유 요청은 TCP 주소가 Vercel이라, 방문자 IP를 route.ts가 따로 넘긴다."""
    headers = {"x-proxy-secret": SECRET, "x-client-ip": "203.0.113.7", "x-forwarded-for": "34.1.1.1"}
    assert rate_limit_key(headers, "10.0.0.2", SECRET) == ("203.0.113.7", "proxy")


def test_비밀값이_틀리면_X_Client_IP를_믿지_않는다():
    """비밀값 없이 믿으면 요청마다 X-Client-IP를 바꿔 제한을 피할 수 있다."""
    headers = {"x-proxy-secret": "wrong", "x-client-ip": "1.2.3.4", "x-forwarded-for": "198.51.100.9"}
    assert rate_limit_key(headers, "10.0.0.2", SECRET) == ("198.51.100.9", "forwarded")


def test_서버에_비밀값이_없으면_X_Client_IP를_무시한다():
    """PROXY_SECRET을 설정하지 않은 환경(로컬·compose)에서 빈 비밀값끼리 일치로 통과하면 안 된다."""
    headers = {"x-proxy-secret": "", "x-client-ip": "1.2.3.4"}
    assert rate_limit_key(headers, "10.0.0.2", None) == ("10.0.0.2", "peer")
    assert rate_limit_key(headers, "10.0.0.2", "") == ("10.0.0.2", "peer")


def test_X_Forwarded_For는_맨_오른쪽을_쓴다():
    """왼쪽 값은 클라이언트가 보낸 그대로라 위조된다. 맨 오른쪽은 우리 쪽 프록시가 붙인 접속 주소다."""
    headers = {"x-forwarded-for": "1.2.3.4, 5.6.7.8, 198.51.100.9"}
    assert rate_limit_key(headers, "10.0.0.2", None) == ("198.51.100.9", "forwarded")


def test_IP가_아닌_값은_건너뛴다():
    headers = {"x-proxy-secret": SECRET, "x-client-ip": "not-an-ip", "x-forwarded-for": "garbage"}
    assert rate_limit_key(headers, "10.0.0.2", SECRET) == ("10.0.0.2", "peer")


def test_IPv6도_받는다():
    headers = {"x-forwarded-for": "2001:db8::1"}
    assert rate_limit_key(headers, None, None) == ("2001:db8::1", "forwarded")


def test_아무_정보가_없으면_unknown():
    assert rate_limit_key({}, None, None) == ("unknown", "peer")
