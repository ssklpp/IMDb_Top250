<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# 프론트엔드 규칙 (`web/`)

> 저장소 루트의 `CLAUDE.md`에서 옮겨온 내용이다. 백엔드 규칙은 그쪽을 볼 것.

## 프론트엔드 레이아웃
`page.tsx`는 `h-[100dvh]` + `header / main(flex-1 overflow-y-auto) / footer` 구조입니다. `h-[100dvh]`(dynamic viewport height)를 사용해 모바일 가상 키보드가 열려도 레이아웃이 올바르게 유지됩니다. 입력 폼은 항상 footer에 고정됩니다. 메시지 목록은 `id`(UUID) 기반 key를 사용하며, 스크롤은 새 메시지 추가 시에만 실행됩니다(`messages.length` 의존). 가상 키보드 열림/닫힘 시에도 `visualViewport` resize 이벤트로 마지막 메시지가 보이도록 스크롤합니다.

헤더 우측에 **다크 모드 토글**(해/달 아이콘)과 **새 대화** 버튼이 있습니다. 다크 모드는 `next-themes`로 관리하며 시스템 설정을 기본값으로 사용하고 새로고침 후에도 유지됩니다. `web/app/providers.tsx`에 `ThemeProvider`가 정의되어 있으며 `layout.tsx`에서 감쌉니다. Tailwind v4 class 기반 다크 모드는 `globals.css`의 `@variant dark (&:where(.dark, .dark *));`로 설정합니다.

`messages.length === 0`일 때 main 영역에 예시 질문 버튼 4개가 표시됩니다. 클릭 시 `submitQuestion(text)`을 직접 호출해 바로 전송됩니다.

AI 응답 버블에는:
- **도구 상태 표시**: 도구 실행 중 버블 상단에 "IMDB Top 250 검색 중..." / "한국 개봉 영화 검색 중..." / "웹 검색 중..." 표시 (파란 펄스 점)
- **에러 버블**: `isError: true`인 메시지는 빨간(`bg-red-50 dark:bg-red-900/30`) 버블로 표시. 복사 버튼 미표시. 에러 코드별 아이콘/라벨이 `errorLabel()` 함수로 부여됨 (`⏱ TIMEOUT`, `🚦 RATE_LIMIT`, `🔌 BACKEND_UNREACHABLE`, `⚠ INTERNAL/BACKEND_ERROR`, `🌐 NETWORK`)
- **다시 시도 버튼**: 재시도 가능한 코드(`RETRYABLE_CODES = TIMEOUT/INTERNAL/BACKEND_UNREACHABLE/BACKEND_ERROR/NETWORK`)에만 표시. `RATE_LIMIT`은 표시하지 않음. `handleRetry()`가 `runRequest()`를 같은 질문으로 재호출
- **복사 버튼**: 데스크톱에서는 hover 시, 모바일(터치 기기)에서는 항상 버블 하단에 표시. 클릭 후 1.5초간 "복사됨" 피드백

## 모바일 최적화
- **뷰포트**: `h-[100dvh]`로 가상 키보드 대응 (`h-screen` 폴백 포함)
- **반응형 타이포그래피**: 헤더 제목 `text-2xl sm:text-4xl`, 입력창 `text-base sm:text-sm` (iOS 자동 줌 방지)
- **터치 타겟**: "새 대화" / 다크 모드 토글 버튼 `min-h-[44px] min-w-[44px]` (WCAG 최소 터치 영역)
- **말풍선 너비**: 모바일 `max-w-[85%]`, 데스크톱 `sm:max-w-[80%]`
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
