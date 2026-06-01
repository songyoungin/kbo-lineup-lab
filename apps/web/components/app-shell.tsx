import Link from "next/link";
import { SidebarNav } from "@/components/sidebar-nav";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="flex w-60 shrink-0 flex-col border-r border-zinc-200 bg-white">
        {/* Brand lockup */}
        <Link
          href="/"
          className="flex items-center gap-2.5 px-5 py-5 transition-opacity hover:opacity-80"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-sm font-extrabold tracking-tight text-white shadow-sm">
            LG
          </span>
          <span className="flex flex-col leading-none">
            <span className="text-sm font-bold text-ink">Lineup Lab</span>
            <span className="mt-0.5 text-[11px] font-medium text-zinc-400">
              트윈스 라인업 분석
            </span>
          </span>
        </Link>

        <div className="px-3 pb-2 pt-1">
          <SidebarNav />
        </div>

        <div className="mt-auto px-5 py-4">
          <p className="text-[11px] leading-relaxed text-zinc-400">
            KBO Lineup Lab
            <br />
            <span className="text-zinc-300">deterministic + LLM lineup</span>
          </p>
        </div>
      </aside>

      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-5xl px-8 py-8">{children}</div>
      </main>
    </div>
  );
}
