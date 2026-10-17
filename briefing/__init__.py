"""주간 박스오피스 브리핑 — 사람이 승인해야 발송되는 그래프(보강 계획 3단계).

    초안(make_draft) → 승인 대기(request_approval, interrupt) → 발송(deliver)

- 그래프 상태는 체크포인터(Postgres)에, 승인·발송 상태는 briefing_runs 테이블에 둔다. 트리거하는 쪽과
  승인하는 쪽이 다른 요청(때로는 다른 프로세스)이라 두 곳 모두 프로세스 밖에 있어야 한다.
- 중복 발송을 막는 건 그래프가 아니라 briefing_runs의 조건부 UPDATE다(store.py). 웹훅에는 중복 방지 키가 없다.
- Postgres(DATABASE_URL)가 있을 때만 켜진다. Railway 배포는 SQLite라 꺼져 있다.

3단계에서는 초안을 KOBIS 주간 박스오피스 원문으로 만들고, 발송은 기록만 한다(LogSender).
LLM 작성·숫자 검증·Discord 발송은 4단계에서 이 자리에 들어간다.
"""
