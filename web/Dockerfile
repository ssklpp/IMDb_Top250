# 프론트엔드(web) 이미지. docker-compose.yml에서 쓴다. Vercel은 이 파일을 쓰지 않는다.
# next.config.ts의 output: "standalone"으로 만든 server.js 하나로 실행한다.

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# next/font/google이 빌드 중에 글꼴을 내려받아 이미지에 넣는다(실행 시에는 외부 요청 없음).
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# standalone은 public과 .next/static을 복사하지 않는다(Next 문서: CDN이 맡는다고 가정).
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
