# 도구 규칙 (`agent.py`의 3개 도구)

> `imdb_search` / `kobis_search` / `web_search`를 고칠 때 먼저 읽을 것.
> 에러 계약(`[TOOL_ERROR code=…]`)과 코드 목록은 `CLAUDE.md`에 있다.

## KOBIS 도구 모드 (`search_type`)
`kobis_search`는 6개 모드를 지원합니다. 모두 KOBIS 오픈API 실호출로 검증되었습니다.

| 모드 | 엔드포인트 | query 의미 |
|---|---|---|
| `movie` | `movie/searchMovieList.json` | 영화 제목 (여러 후보 나열) |
| `detail` | `movie/searchMovieList` → `movie/searchMovieInfo.json` | 영화 제목 (상세정보 1편) |
| `daily` | `boxoffice/searchDailyBoxOfficeList.json` | YYYYMMDD |
| `weekly` | `boxoffice/searchWeeklyBoxOfficeList.json` `weekGb=0` | YYYYMMDD (주간 월~일) |
| `weekend` | 같은 URL, `weekGb=1` | YYYYMMDD (주말 금~일) |
| `weekday` | 같은 URL, `weekGb=2` | YYYYMMDD (주중 월~목) |

**KOBIS는 진행 중인 기간을 주지 않습니다.** 일별은 다음 날, 주간·주말·주중은 해당 주가 끝난 뒤에
공개됩니다(실호출로 확인). 오늘 날짜로 조회하면 빈 목록이 오므로 `[TOOL_ERROR code=NO_DATA]`로
반환하고, 연도를 바꾸지 말고 하루/일주일 이전으로 재호출하라는 힌트를 함께 줍니다. 이 힌트가 없을
때 LLM이 연도만 1년 낮춰 재호출해 작년 데이터를 "지난 주"라고 답한 적이 있습니다.

박스오피스 4종은 단일 분기로 통합되어 있고, 헤더 라벨은 하드코딩이 아니라 응답의 `boxOfficeResult.boxofficeType`을 사용합니다(KOBIS가 "주말 박스오피스" 같은 한글 라벨을 직접 제공).

**`detail` 모드의 2단계 조회**: `searchMovieInfo`는 `movieCd`(영화코드)로만 조회되고 영화명으로는 안 됩니다. 그래서 `detail`은 내부에서 목록조회 → movieCd 해석 → 상세조회를 자동 처리하며, LLM은 영화명으로 한 번만 호출하면 됩니다. query가 8자리 숫자면 movieCd로 간주해 1단계를 건너뜁니다.

**`pick_movie()`의 해석 규칙** (`kobis_format.py`) — KOBIS 목록은 관련도순이 아니라서 1위가 정답이 아닌 경우가 많습니다("기생충" 검색 시 1위가 "마약 기생충"). 다음 순서로 좁힙니다:
1. 공백·대소문자 무시 **정확 일치**
2. `prdtStatNm == "개봉"` (개봉 완료작)
3. `repNationNm == "한국"` (한국 제작 — 이게 없으면 "올드보이"가 2003년 박찬욱판이 아니라 2013년 스파이크 리 리메이크로 해석됨)
4. 최신 `openDt`

선택되지 않은 후보는 출력 말미에 `(동명/유사 제목 후보: ...)`로 덧붙여 LLM이 오선택을 인지할 수 있게 합니다.

**출력 상한** — KOBIS 상세정보는 `actors`를 90건, `staffs`를 625건까지 반환합니다. `staffs`는 전량 제외(VFX 아티스트·투자 등 크루 노이즈), `actors`는 `DETAIL_MAX_ACTORS=8`건까지만 `이름(배역)` 형태로 렌더링하고 나머지는 "외 N명"으로 요약합니다. `companys`는 `companyPartNm`으로 제작사·배급사만 필터합니다.

## 웹 검색 도구 (`web_search`)
`langchain_tavily.TavilySearch`를 쓰지 않고 `agent.py`의 `_tavily_search()`가 Tavily REST API를
`requests`로 직접 호출한다. **`TavilySearch`로 되돌리지 말 것.** 바꾼 이유가 전부 실측으로 확인된 문제다.

| 문제 (TavilySearch) | 실측 | 지금 |
|---|---|---|
| LLM에 파라미터 9개(`include_domains`, `time_range` 등)를 노출 | 도구 정의 **1,521토큰**. LLM 호출마다 전송되고 질문 하나에 호출이 2번이라 약 3,000토큰이 매번 나감 | `query` 하나, **82토큰** (도구 정의 합계 2,132 → 692) |
| 결과 dict를 통째로 문자열화 (`score`, `raw_content`, `images`, `request_id` …) | 결과 **1,546토큰** | `format_web_results()`가 title·url·content만, 본문 1,000자 상한 → **1,059토큰** |
| 생성 시점에 `TAVILY_API_KEY` 필수 | 키가 없으면 **`agent.py` import부터 실패** — 헬스체크의 "선택 키" 설계와 모순 | 키가 없으면 도구만 `MISSING_API_KEY` 반환 |
| 동기 경로 `requests.post`에 timeout 없음 | 응답이 멈추면 요청 제한(120초)까지 붙잡힘 | `WEB_TIMEOUT=15`, 연결 문제만 2회 재시도 |

- **기존 도구를 새 `@tool` 안에서 `invoke()`로 감싸는 방식도 안 된다.** 중첩 실행이 도구 이벤트로
  잡혀 `\x1ftool:` 센티넬과 `tool_calls` 집계가 두 번씩 나간다. 그래서 래핑이 아니라 직접 호출이다.
- 결과 형태는 `sources.py`의 `format_web_results()`(만드는 쪽)와 `_web_sources()`(읽는 쪽)가 공유한다.
  한쪽만 바꾸면 에러 없이 출처 칩만 사라지므로 두 함수를 같은 모듈에 두고 왕복 테스트로 고정했다.
- 검색어는 사용자 질문에서 파생되므로 로그에는 `query_len`만 남긴다.

## 도구 입력 검증
> 에러 포맷(`[TOOL_ERROR code=…]`)과 코드 목록은 `CLAUDE.md`에 있다.
`kobis_search`는 Pydantic `KobisInput` 스키마(`agent.py`)로 인자를 검증합니다.
- `search_type`: `Literal["movie", "detail", "daily", "weekly", "weekend", "weekday"]` — 잘못된 값 시 LangChain이 `ValidationError`를 ToolMessage로 변환해 LLM에 반환 → LLM이 자가 정정 후 재호출
- `open_start_dt` / `open_end_dt`: `field_validator`로 4자리 숫자 검증
- 박스오피스 모드의 `query`: `is_valid_date()`(`kobis_format.py`)로 **8자리 숫자이면서 실제 달력에 존재하는 날짜**인지 검증. 두 조건을 모두 봐야 합니다 — KOBIS가 `99999999`에도 에러 대신 엉뚱한 데이터를 반환하고, 반대로 `strptime`만 쓰면 제로 패딩에 관대해서 7자리 `'2026091'`을 2026-09-01로 통과시킵니다(실제로 있었던 버그). `detail`은 제목을 받으므로 이 검증 대상이 아닙니다.

> 날짜 검증을 Pydantic `model_validator`로 옮기지 말 것. `search_type` 의존 규칙이라 교차 필드 검증이 필요한데, 옮기면 에러 표면이 `[TOOL_ERROR code=INVALID_DATE]`에서 Pydantic `ValidationError`로 바뀌어 이 문서와 `SYSTEM_PROMPT`의 서술이 전부 어긋납니다.

> **헬퍼에서 "못 찾음"을 `raise ValueError`로 신호하지 말 것.** `kobis_search`의 `except (KeyError, ValueError, TypeError)`가 잡아서 `PARSE_ERROR`로 오분류합니다. `None`/빈 dict를 반환하고 호출부에서 `tool_error()`를 반환하세요. 같은 이유로 헬퍼는 `_kobis_get`의 예외를 잡지 않고 그대로 전파시켜 기존 4종 except가 처리하게 합니다.

## 신뢰성 (Retry / Fallback)
- `_kobis_get()`은 `tenacity`로 KOBIS API 호출을 최대 3회까지 지수 백오프로 재시도(`RequestException`만 대상)
- `_tavily_search()`는 **최대 2회, 연결 문제(`Timeout`/`ConnectionError`)만** 재시도. Tavily는 호출마다 크레딧을 쓰고 HTTP 오류는 다시 보내도 같기 때문
- 도구별 타임아웃: `KOBIS_TIMEOUT=10s`, `WEB_TIMEOUT=15s`. 전체 요청 타임아웃: `REQUEST_TIMEOUT_S=120` (`server.py`)
- 폴백은 명시적 코드가 아닌 LLM 판단으로 처리됨 — 도구 에러 메시지에 "web_search로 대신 시도해보세요" 같은 힌트를 포함
