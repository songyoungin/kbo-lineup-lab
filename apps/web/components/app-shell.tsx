import Link from "next/link";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex">
      <aside className="w-56 shrink-0 border-r border-zinc-200 bg-zinc-50 p-4">
        <div className="font-bold text-xs uppercase tracking-wider text-zinc-400 mb-4 px-2">
          KBO Lineup Lab
        </div>
        <nav className="space-y-0.5 text-sm">
          <Link
            href="/"
            className="block px-2 py-1.5 rounded text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
          >
            팀 홈
          </Link>
          <Link
            href="/pregame"
            className="block px-2 py-1.5 rounded text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
          >
            프리게임 평가
          </Link>
          <Link
            href="/postgame"
            className="block px-2 py-1.5 rounded text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
          >
            포스트게임 리뷰
          </Link>
          <Link
            href="/admin"
            className="block px-2 py-1.5 rounded text-zinc-700 hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
          >
            파이프라인 상태
          </Link>
        </nav>
      </aside>
      <main className="flex-1 p-6 overflow-auto">{children}</main>
    </div>
  );
}
