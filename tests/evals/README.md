# 에이전트 평가 (Evals)

골든 데이터셋 기반 회귀 평가. 프롬프트/모델/도구 변경 시 품질이 떨어지지 않는지 자동으로 검증합니다.

## 구조
- `golden_dataset.json` — 평가 항목(질문, 기대 도구, 기대 키워드, rubric)
- `run_evals.py` — 평가 러너

## 실행
프로젝트 루트에서:

```bash
# 전체 평가 (LLM-as-judge 포함, OpenAI API 사용)
python -m tests.evals.run_evals

# judge 생략 (채점 비용만 절약 — 에이전트 본체는 실제로 호출되므로 무료가 아니다)
python -m tests.evals.run_evals --skip-judge

# 특정 항목만 실행
python -m tests.evals.run_evals --ids imdb-001 kobis-001
```

## 평가 기준
각 항목은 다음을 검증합니다:
1. **도구 호출(tools_ok)** — `expected_tools` 중 하나라도 호출했는지
2. **키워드(keywords_ok)** — `expected_keywords` 중 하나(또는 전부) 포함됐는지
3. **judge_score** — (옵션) GPT 채점관이 rubric 기준 1~5점

모두 통과 + judge ≥ 4점이면 PASS.

> **judge 호출이 실패하면 해당 항목은 실패 처리됩니다.** 예전에는 `judge_score`가 `None`으로
> 남아 자동 통과되면서 평가가 조용히 무력화되는 fail-open 구조였습니다. `judge_failed` 플래그가
> 이 구멍을 막습니다.

> **`--skip-judge`는 완전 무료가 아닙니다.** 채점 비용만 없앨 뿐 에이전트 본체는 모든 항목에 대해
> 실제로 OpenAI·Tavily·KOBIS를 호출합니다. 비용 0으로 돌릴 수 있는 것은 `pytest tests/unit/`뿐입니다.

## 새 항목 추가
`golden_dataset.json`에 객체 추가:
```json
{
  "id": "고유-id",
  "question": "사용자 질문",
  "expected_tools": ["kobis_search"],
  "expected_keywords": ["키워드1", "키워드2"],
  "expected_keywords_any": true,
  "rubric": "이 답변이 가져야 할 조건을 한 문장으로"
}
```

`expected_keywords_any`(any/all)와 `rubric` 작성을 잊지 말 것. 거절 규칙처럼 **경계**를 다루는
항목은 양쪽 방향을 모두 세운다 — `refusal-001`(거절해야 함)과 `refusal-002`(거절하면 안 됨).

## 종료 코드
- `0` — 전체 통과
- `1` — 하나 이상 실패 (CI에서 회귀 차단용)
