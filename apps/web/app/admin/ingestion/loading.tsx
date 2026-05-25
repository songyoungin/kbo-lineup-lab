// Next.js 자동 로딩 UI — 스켈레톤 플레이스홀더
export default function AdminIngestionLoading() {
  return (
    <div className="space-y-8 max-w-5xl animate-pulse">
      {/* 헤더 스켈레톤 */}
      <div className="space-y-2">
        <div className="h-6 w-56 bg-zinc-200 rounded" />
        <div className="h-4 w-80 bg-zinc-100 rounded" />
      </div>

      {/* 섹션 제목 스켈레톤 */}
      <div className="space-y-3">
        <div className="h-4 w-28 bg-zinc-200 rounded" />
        {/* 테이블 스켈레톤 */}
        <div className="rounded-md border border-zinc-200 overflow-hidden">
          <div className="h-9 bg-zinc-50 border-b border-zinc-200" />
          {[0, 1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="flex gap-4 px-3 py-2 border-b border-zinc-100 last:border-0"
            >
              <div className="h-4 w-8 bg-zinc-200 rounded" />
              <div className="h-4 w-48 bg-zinc-100 rounded" />
              <div className="h-5 w-16 bg-zinc-200 rounded-full" />
              <div className="h-4 w-24 bg-zinc-100 rounded" />
              <div className="h-4 w-24 bg-zinc-100 rounded" />
            </div>
          ))}
        </div>
      </div>

      {/* 두 번째 섹션 스켈레톤 */}
      <div className="space-y-3">
        <div className="h-4 w-36 bg-zinc-200 rounded" />
        <div className="rounded-md border border-zinc-200 overflow-hidden">
          <div className="h-9 bg-zinc-50 border-b border-zinc-200" />
          {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
            <div
              key={i}
              className="flex gap-4 px-3 py-2 border-b border-zinc-100 last:border-0"
            >
              <div className="h-4 w-24 bg-zinc-100 rounded" />
              <div className="h-5 w-16 bg-zinc-200 rounded-full" />
              <div className="h-4 w-12 bg-zinc-100 rounded" />
              <div className="h-4 w-12 bg-zinc-100 rounded" />
              <div className="h-4 w-12 bg-zinc-100 rounded" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
