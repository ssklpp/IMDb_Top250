"""MCP 서버와의 세션 하나를 계속 붙잡아 두고, 끊기면 다시 연결한다.

langchain-mcp-adapters의 기본(get_tools)은 도구 호출마다 세션을 새로 연다. 같은 PC에서도 초기화 왕복 때문에
imdb_sql이 3ms → 281ms, imdb_search가 156ms → 436ms로 느려졌다(docs/bug-log.md B-013). 세션을 붙잡으면
6ms·164ms로 로컬과 거의 같다. 대신 MCP 서버가 재시작되면 그 세션은 죽으므로, 다시 여는 일을 여기서 맡는다.

설계
- 세션은 전용 백그라운드 태스크(_run)가 열고 닫는다. MCP SDK의 연결은 anyio 태스크 그룹이라, 요청 처리 태스크에서
  열고 다른 태스크(lifespan)에서 닫으면 "다른 태스크에서 cancel scope를 나갔다"는 에러가 난다.
- 도구 호출이 예외로 실패하면 연결 문제로 본다 — 도구 자체의 실패는 예외가 아니라 isError 결과로 온다.
  세션을 버리고 새로 연결한 뒤 **한 번만** 다시 호출한다. 도구 4개는 모두 조회라 다시 불러도 안전하다.
- 어댑터가 세션에서 쓰는 메서드는 list_tools와 call_tool 둘뿐이라 그 둘만 흉내 낸다(load_mcp_tools에 넘긴다).

mcp 패키지를 import하지 않는다 — 클라이언트(MultiServerMCPClient)는 밖에서 받는다. 그래서 재연결 로직을
가짜 세션으로 tests/unit에서 검사할 수 있다.
"""

import asyncio

from logging_config import get_logger

log = get_logger("mcp_client")

CONNECT_TIMEOUT_S = 30
RECONNECT_DELAY_S = 1.0


class PersistentMcpSession:
    def __init__(self, client, server_name: str):
        self._client = client
        self._server_name = server_name
        self._session = None
        self._ready = asyncio.Event()
        self._broken = asyncio.Event()
        self._closing = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """연결이 될 때까지 기다린다. CONNECT_TIMEOUT_S 안에 안 되면 TimeoutError."""
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._ready.wait(), CONNECT_TIMEOUT_S)

    async def close(self) -> None:
        self._closing = True
        self._broken.set()
        if self._task:
            await self._task

    async def _run(self) -> None:
        while not self._closing:
            try:
                async with self._client.session(self._server_name) as session:
                    self._session = session
                    self._ready.set()
                    log.info("mcp.session_open", server=self._server_name)
                    await self._broken.wait()
            except Exception as e:
                # 서버가 내려가 연결 안의 태스크 그룹이 깨지면 여기로 온다. 다음 바퀴에서 다시 연결한다.
                log.warning("mcp.session_error", server=self._server_name, error=type(e).__name__)
            finally:
                self._session = None
                self._ready.clear()
                self._broken.clear()
            if not self._closing:
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def _current(self):
        await asyncio.wait_for(self._ready.wait(), CONNECT_TIMEOUT_S)
        return self._session

    async def _with_retry(self, method: str, *args, **kwargs):
        session = await self._current()
        try:
            return await getattr(session, method)(*args, **kwargs)
        except Exception as e:
            # 이미 다른 요청이 재연결을 시작했으면(세션이 바뀌었으면) 그 결과를 기다리기만 한다.
            if self._session is session:
                self._ready.clear()
                self._broken.set()
            log.warning("mcp.reconnect", method=method, error=type(e).__name__)
            session = await self._current()
            return await getattr(session, method)(*args, **kwargs)

    async def list_tools(self, *args, **kwargs):
        return await self._with_retry("list_tools", *args, **kwargs)

    async def call_tool(self, *args, **kwargs):
        return await self._with_retry("call_tool", *args, **kwargs)

    async def read_resource(self, *args, **kwargs):
        """브리핑이 KOBIS 원본(kobis://weekly/…)을 읽을 때 쓴다. 도구 호출과 같은 세션·재연결."""
        return await self._with_retry("read_resource", *args, **kwargs)
