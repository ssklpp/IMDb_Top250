import type { Metadata } from "next";
import { Hahmlet, IBM_Plex_Sans_KR } from "next/font/google";
import "./globals.css";
import Providers from "./providers";

// 제목과 입장권 숫자용 한글 세리프
const hahmlet = Hahmlet({
  variable: "--font-hahmlet",
  subsets: ["latin"],
});

// 본문용. 한글 글리프는 unicode-range로 나뉘어 필요한 조각만 내려받는다.
const plexKr = IBM_Plex_Sans_KR({
  variable: "--font-body",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "영화 챗봇",
  description: "IMDB Top 250, 한국 박스오피스, 웹을 찾아 출처와 함께 답하는 영화 챗봇",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" suppressHydrationWarning className={`${hahmlet.variable} ${plexKr.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
