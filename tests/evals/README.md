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

# judge 생략 (도구/키워드 검증만, 빠르고 비용 0)
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

## 종료 코드
- `0` — 전체 통과
- `1` — 하나 이상 실패 (CI에서 회귀 차단용)
