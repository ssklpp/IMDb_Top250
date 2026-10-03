<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# 프론트엔드 규칙 (`web/`)

> 저장소 루트의 `CLAUDE.md`에서 옮겨온 내용이다. 백엔드 규칙은 그쪽을 볼 것.

## 프론트엔드 레이아웃
`page.tsx`는 `h-[100dvh]` + `header / main(flex-1 overflow-y-auto) / footer` 구조입니다. `h-[100dvh]`(dynamic viewport height)를 사용해 모바일 가상 키보드가 열려도 레이아웃이 올바르게 유지됩니다. 입력 폼은 항상 footer에 고정됩니다. 메시지 목록은 `id`(UUID) 기반 key를 사용하며, 스크롤은 새 메시지 추가 시에만 실행됩니다(`messages.length` 의존). 가상 키보드 열림/닫힘 시에도 `visualViewport` resize 이벤트로 마지막 메시지가 보이도록 스크롤합니다.

헤더 우측에 **다크 모드 토글**(해/달 아이콘)과 **새 대화** 버튼이 있습니다. 다크 모드는 `next-themes`로 관리하며 시스템 설정을 기본값으로 사용하고 새로고침 후에도 유지됩니다. `web/app/providers.tsx`에 `ThemeProvider`가 정의되어 있으며 `layout.tsx`에서 감쌉니다. Tailwind v4 class 기반 다크 모드는 `globals.css`의 `@variant dark (&:where(.dark, .dark *));`로 설정합니다.

`messages.length === 0`일 때 main 영역에 큰 제목("어떤 영화가 궁금하세요?"), 한 줄 설명, 예시 질문 4줄이 표시됩니다. 각 줄 오른쪽에 답이 나올 곳(IMDB 순위표·영화진흥위원회 등)을 입장권 색 표시와 함께 보여줍니다. 클릭 시 `submitQuestion(text)`을 직접 호출해 바로 전송됩니다.

## 디자인 — 출처 입장권
화면에서 눈에 띄는 요소는 **답변 아래의 출처 입장권 하나**다. KOBIS가 '영화관입장권통합전산망'이고, 입장권이
입장의 증거이듯 이 표는 답의 근거라는 뜻이다. 나머지(헤더·질문·답변)는 조용하게 둔다.

- **색**: `globals.css`의 `:root` / `.dark` 변수만 쓴다(`bg-page`, `text-ink`, `text-ink-soft`, `border-line`, `text-stamp`). 컴포넌트에 `gray-*`·`blue-*`를 새로 쓰지 말 것.
- **입장권 종이색은 도구별**: IMDB(`imdb_search`·`imdb_sql`) 노랑, KOBIS 분홍, 웹 초록. 다크 모드에서도 종이는 밝고 글자는 짙다. 도구 실행 중 표시(`.swatch`)도 같은 색으로 깜박인다.
- **글꼴**: 제목·입장권 숫자는 Hahmlet(`font-display`), 본문은 IBM Plex Sans KR(`font-sans`). 둘 다 `layout.tsx`의 `next/font/google`.
- **입장권 내용은 `stubParts()`가 라벨을 해석해 만든다.** IMDB는 `sources._imdb_sources()`의 `"IMDB #<순위> <제목>"` 형식에서 순위를 꺼내 "3위"로 크게 쓴다. 형식이 바뀌면 순위 없이 라벨만 나오는 대체 표시로 떨어진다(에러 없음).
- **움직임은 하나**: 입장권이 70ms 간격으로 차례로 내려오며 나타난다. `prefers-reduced-motion`이면 끈다.
- 한국어 줄바꿈은 `body`의 `word-break: keep-all`(어절 단위).

질문은 오른쪽 짙은 알약, 답변은 말풍선 없이 본문 폭 그대로 읽는 글입니다. 답변 영역에는:
- **도구 상태 표시**: 도구 실행 중 답변 위에 "IMDB Top 250 검색 중..." / "한국 개봉 영화 검색 중..." / "웹 검색 중..." 표시 (도구 색 `.swatch` 펄스)
- **에러 표시**: `isError: true`인 메시지는 왼쪽에 빨간 세로줄(`border-stamp`)이 있는 블록으로 표시. 복사 버튼 미표시. 에러 코드별 아이콘/라벨이 `errorLabel()` 함수로 부여됨 (`⏱ TIMEOUT`, `🚦 RATE_LIMIT`, `🔌 BACKEND_UNREACHABLE`, `⚠ INTERNAL/BACKEND_ERROR`, `🌐 NETWORK`)
- **다시 시도 버튼**: 재시도 가능한 코드(`RETRYABLE_CODES = TIMEOUT/INTERNAL/BACKEND_UNREACHABLE/BACKEND_ERROR/NETWORK`)에만 표시. `RATE_LIMIT`은 표시하지 않음. `handleRetry()`가 `runRequest()`를 같은 질문으로 재호출
- **복사 버튼**: 데스크톱에서는 hover 시, 모바일(터치 기기)에서는 항상 출처 아래에 표시. 클릭 후 1.5초간 "복사됨" 피드백

## 모바일 최적화
- **뷰포트**: `h-[100dvh]`로 가상 키보드 대응 (`h-screen` 폴백 포함)
- **반응형 타이포그래피**: 빈 화면 제목 `text-[2.25rem] sm:text-5xl`, 입력창 `text-base sm:text-sm` (iOS 자동 줌 방지)
- **터치 타겟**: "새 대화" / 다크 모드 토글 버튼 `min-h-[44px] min-w-[44px]` (WCAG 최소 터치 영역)
- **질문 말풍선 너비**: 모바일 `max-w-[85%]`, 데스크톱 `sm:max-w-[80%]` (답변은 말풍선 없이 전체 폭)
- **복사 버튼**: `[@media(hover:none)]:opacity-100`으로 터치 기기에서 항상 표시
- **패딩**: footer/input/button 모바일 `py-2`, 데스크톱 `sm:py-3`

## 마크다운 렌더링
에이전트 답변은 `react-markdown`으로 렌더링됩니다. `components` prop으로 Tailwind 클래스를 직접 지정합니다. 지원 요소: `p`, `ul`, `ol`, `li`, `strong`, `h1`–`h3`, `code`(인라인), `blockquote`, `hr`.

## 프론트엔드 프록시 에러 (route.ts)
`web/app/api/chat/route.ts`는 백엔드 호출 결과를 JSON 에러로 변환합니다:
- 빈 질문 → 400 `{code: "EMPTY_QUESTION"}`
- 백엔드 도달 불가 → 503 `{code: "BACKEND_UNREACHABLE"}`
- 백엔드 429 → 429 `{code: "RATE_LIMIT"}`
- 기타 백엔드 비-2xx → `{code: "BACKEND_ERROR"}`
- 정상 → 백엔드 스트림 + `X-Request-Id`/`X-Session-Id` 헤더 패스스루

프론트엔드(`page.tsx`)는 이 `code`로 라벨/재시도 가능 여부를 결정합니다.

## Next.js 16 주의사항
Next.js 16은 이전 버전과 API, 파일 구조가 다릅니다. `web/` 코드 수정 시 반드시 `node_modules/next/dist/docs/`의 가이드를 먼저 확인하세요 (이 문서 맨 위 블록과 같은 내용).
