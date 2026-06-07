import type { Metadata } from "next";
import { IBM_Plex_Sans_KR } from "next/font/google";
import { AppShell } from "@/components/app-shell";
import "./globals.css";

// 단일 서체 — IBM Plex Sans KR (한글은 unicode-range 슬라이스로 자동 포함).
const plexSansKr = IBM_Plex_Sans_KR({
  variable: "--font-plex-sans-kr",
  weight: ["400", "500", "700"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "KBO Lineup Lab",
  description: "LG Twins lineup analysis dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className={`${plexSansKr.variable} h-full antialiased`}>
      <body className="min-h-full font-sans">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
