import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Docker 이미지(web/Dockerfile)용. .next/standalone에 server.js와 필요한 node_modules만 모은다.
  // Vercel 배포에는 영향이 없다.
  output: "standalone",
};

export default nextConfig;
