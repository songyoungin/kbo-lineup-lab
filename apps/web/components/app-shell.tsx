import Link from "next/link";
import { SidebarNav } from "@/components/sidebar-nav";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="pinstripe flex w-60 shrink-0 flex-col border-r border-rule bg-surface">
        {/* Masthead-style brand lockup */}
        <Link
          href="/"
          className="flex items-center gap-3 px-5 pb-4 pt-6 transition-opacity hover:opacity-80"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-sm bg-brand-600 text-sm font-bold tracking-tight text-white shadow-sm">
            LG
          </span>
          <span className="flex flex-col leading-none">
            <span className="text-[17px] font-bold tracking-tight text-ink">
              Lineup Lab
            </span>
            <span className="mt-1 text-[10px] font-medium uppercase tracking-[0.16em] text-zinc-400">
              트윈스 라인업 분석
            </span>
          </span>
        </Link>

        {/* Newspaper masthead divider */}
        <div className="rule-double mx-5 mb-3" />

        <div className="px-3 pb-2">
          <SidebarNav />
        </div>

        <div className="mt-auto px-5 py-4">
          <div className="mb-3 h-px bg-rule" />
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
