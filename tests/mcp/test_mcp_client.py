"""mcp_client.PersistentMcpSession 테스트. 가짜 세션으로 재연결만 본다(네트워크 없음).

tests/unit이 아니라 여기 있는 이유: mcp_client가 logging_config(structlog)를 import해서, pytest만 설치하는
CI python 잡에서는 import가 실패한다(B-015). 전체 의존성이 깔린 deps 잡이 tests/mcp를 돌린다.

세션을 붙잡아 두면 도구 호출이 281ms → 6ms로 빨라지지만(B-013), MCP 서버가 재시작되면 그 세션은 죽는다.
그때 요청이 실패하지 않고 새 세션으로 다시 붙는지가 이 테스트의 목적이다.
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import mcp_client  # noqa: E402
from mcp_client import PersistentMcpSession  # noqa: E402


class _FakeSession:
    def __init__(self, n, fail_calls=0):
        self.n = n
        self.fail_calls = fail_calls
        self.calls = 0

    async def call_tool(self, name, args, **kwargs):
        self.calls += 1
        if self.fail_calls:
            self.fail_calls -= 1
            raise ConnectionError("server restarted")
        return f"session{self.n}:{name}"

    async def list_tools(self, cursor=None):
        return f"tools from session{self.n}"


class _FakeClient:
    """session()을 열 때마다 새 가짜 세션을 준다. 첫 세션은 fail_first번 실패한다."""

    def __init__(self, fail_first=0):
        self.opened = []
        self.closed = 0
        self.fail_first = fail_first

    @asynccontextmanager
    async def session(self, name):
        s = _FakeSession(len(self.opened) + 1, self.fail_first if not self.opened else 0)
        self.opened.append(s)
        try:
            yield s
        finally:
            self.closed += 1


def _run(coro):
    mcp_client.RECONNECT_DELAY_S = 0  # 테스트에서는 기다리지 않는다
    return asyncio.run(coro)


def test_세션_하나를_여러_호출이_같이_쓴다():
    """호출마다 세션을 새로 열면 느려진다(B-013). 열린 세션은 하나여야 한다."""
    async def go():
        client = _FakeClient()
        s = PersistentMcpSession(client, "movie")
        await s.start()
        results = await asyncio.gather(*(s.call_tool("imdb_sql", {}) for _ in range(5)))
        await s.close()
        return client, results

    client, results = _run(go())
    assert len(client.opened) == 1
    assert results == ["session1:imdb_sql"] * 5
    assert client.closed == 1


def test_연결이_끊기면_새_세션으로_한_번_다시_부른다():
    """MCP 서버가 재시작되면 옛 세션 호출은 예외가 난다. 사용자 요청이 실패하면 안 된다."""
    async def go():
        client = _FakeClient(fail_first=1)
        s = PersistentMcpSession(client, "movie")
        await s.start()
        result = await s.call_tool("kobis_search", {})
        await s.close()
        return client, result

    client, result = _run(go())
    assert result == "session2:kobis_search"
    assert len(client.opened) == 2


def test_다시_불러도_실패하면_예외를_올린다():
    """무한히 재시도하지 않는다. 두 번째도 실패하면 에이전트가 에러로 처리한다."""
    class AlwaysFail(_FakeClient):
        @asynccontextmanager
        async def session(self, name):
            s = _FakeSession(len(self.opened) + 1, fail_calls=99)
            self.opened.append(s)
            yield s

    async def go():
        s = PersistentMcpSession(AlwaysFail(), "movie")
        await s.start()
        try:
            await s.call_tool("imdb_sql", {})
        except ConnectionError:
            return "raised"
        finally:
            await s.close()

    assert _run(go()) == "raised"


def test_동시에_실패해도_재연결은_한_번만():
    """여러 요청이 같은 죽은 세션에서 동시에 실패해도 세션을 여러 개 열지 않는다."""
    async def go():
        client = _FakeClient(fail_first=3)
        s = PersistentMcpSession(client, "movie")
        await s.start()
        results = await asyncio.gather(*(s.call_tool("imdb_sql", {}) for _ in range(3)))
        await s.close()
        return client, results

    client, results = _run(go())
    assert results == ["session2:imdb_sql"] * 3
    assert len(client.opened) == 2


def test_list_tools도_같은_세션을_쓴다():
    async def go():
        s = PersistentMcpSession(_FakeClient(), "movie")
        await s.start()
        out = await s.list_tools(cursor=None)
        await s.close()
        return out

    assert _run(go()) == "tools from session1"
