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

> **평가도 서버와 같은 입력을 씁니다.** 질문 앞에 오늘 날짜 문장(`format_date_context(today_kst())`)을
> `SystemMessage`로 넣고, 채점관 프롬프트에도 같은 날짜를 줍니다. `kobis-006`("지난 주 박스오피스")처럼
> 정답이 날짜에 따라 바뀌는 항목은 채점관이 오늘을 모르면 판정할 수 없기 때문입니다.
> 그런 항목의 rubric은 **특정 날짜를 적지 말고** "주어진 오늘 날짜의 직전 주"처럼 상대적으로 쓸 것 —
> 날짜를 박아 두면 다음 주부터 틀린 기준이 됩니다.

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

### `imdb_search` 항목에는 `corpus_evidence`

```json
  "expected_tools": ["imdb_search"],
  "expected_keywords": ["프랭크", "다라본트"],
  "corpus_evidence": ["The Shawshank Redemption", "Frank Darabont"],
```

답의 근거가 되는 **영어** 문자열을 적는다(코퍼스가 영어다). `tests/unit/test_imdb_data.py`가
이 필드가 있는 모든 항목에 대해 문자열이 코퍼스에 실제로 있는지 확인하고, `expected_tools`에
`imdb_search`가 있는데 이 필드가 없으면 실패한다.

이유: 평가는 "답이 맞는가"만 보고 "답이 코퍼스에서 왔는가"는 보지 않는다. 예전 PDF는 칸 너비에서
글자가 잘려 'Shawshank'가 한 번도 없었는데, 모델이 사전지식으로 답해 IMDB 항목이 전부 통과했다.

`imdb_sql` 항목에는 강제하지 않는다. "1990년대 몇 편"의 답 39는 이 CSV에서만 나오는 값이라
모델이 기억으로 맞힐 수 없고, 키워드 검사가 곧 도구를 썼다는 증거다.

> 이 검사는 **답이 코퍼스에 있다**만 보장한다. **검색이 그걸 찾아오는지**(적중률)는 별개다.
> 예: 검색어 "쇼생크 탈출 감독"을 그대로 넣으면 상위 8편에 쇼생크가 없다(코퍼스가 영어). 실제로는
> 모델이 제목을 영어로 바꿔 검색해서 적중하지만, 그건 모델 행동이지 보장이 아니다.

## 종료 코드
- `0` — 전체 통과
- `1` — 하나 이상 실패 (CI에서 회귀 차단용)
