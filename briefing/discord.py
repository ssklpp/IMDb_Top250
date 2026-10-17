"""Discord 웹훅 발송. DISCORD_WEBHOOK_URL이 있으면 브리핑을, DISCORD_REVIEW_WEBHOOK_URL이 있으면 승인 요청 알림을 보낸다.

- 웹훅에는 중복 방지 키가 없다. 한 번만 보내는 건 briefing_runs의 claim_send가 맡는다(여기서 재시도하지 않는다 —
  타임아웃이 났어도 Discord는 받았을 수 있다).
- 멘션을 막는다(allowed_mentions). 글 일부는 모델이 쓰므로 "@everyone"이 섞여도 알림이 터지지 않게.
- URL에 토큰이 들어 있으므로 로그·에러 메시지에 URL을 남기지 않는다(예외도 타입만 기록된다).
"""

import httpx

TIMEOUT_S = 10


class DiscordSender:
    def __init__(self, webhook_url: str):
        self._url = webhook_url

    async def send(self, text: str) -> str:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.post(
                self._url,
                params={"wait": "true"},  # 메시지 ID를 받으려면 필요하다(delivery_id로 기록)
                json={"content": text, "allowed_mentions": {"parse": []}},
            )
            resp.raise_for_status()
            return f"discord-{resp.json().get('id', '')}"
