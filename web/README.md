# web — 영화 챗봇 프론트엔드

Next.js 16(App Router) 화면과 백엔드 프록시입니다. 프로젝트 소개와 실행 방법은 저장소 루트의 `README.md`,
프론트엔드 규칙(레이아웃, 출처 입장권 디자인, 관리자 화면, 프록시 에러)은 `AGENTS.md`에 있습니다.

```bash
npm install
npm run dev      # http://localhost:3000 (백엔드는 BACKEND_URL, 기본 http://localhost:8000)
npm run lint
npm run build
```

| 경로 | 하는 일 |
|---|---|
| `app/page.tsx` | 채팅 화면. 스트리밍 응답의 센티넬(`tool:`·`sources:`·`error:`)을 읽어 도구 상태와 출처 입장권을 그린다 |
| `app/api/chat/route.ts` | 백엔드 `/api/chat` 프록시. 방문자 IP와 `PROXY_SECRET`을 함께 넘긴다(레이트리밋) |
| `app/admin/briefings/` | 주간 브리핑 승인 화면 |
| `app/api/admin/briefings/` | 백엔드 `/briefings` 프록시(허용 경로만) |

`public/`은 비어 있습니다(`web/Dockerfile`이 복사하므로 `.gitkeep`으로 폴더를 남겨 둔다).
