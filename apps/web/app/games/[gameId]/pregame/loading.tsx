// Next.js 자동 로딩 UI — 스켈레톤 플레이스홀더
export default function PregameLoading() {
  return (
    <div className="space-y-6 max-w-4xl animate-pulse">
      {/* 헤더 스켈레톤 */}
      <div className="space-y-2">
        <div className="h-6 w-48 bg-zinc-200 rounded" />
        <div className="h-4 w-24 bg-zinc-100 rounded" />
      </div>

      {/* 점수 대결(ScoreDuel) 스켈레톤 */}
      <div className="rounded-md border border-rule bg-surface px-6 py-5 space-y-4">
        <div className="flex items-end justify-between">
          <div className="space-y-2">
            <div className="h-3 w-20 bg-zinc-200 rounded" />
            <div className="h-9 w-28 bg-zinc-200 rounded" />
          </div>
          <div className="space-y-2 flex flex-col items-end">
            <div className="h-3 w-20 bg-zinc-200 rounded" />
            <div className="h-9 w-28 bg-zinc-200 rounded" />
          </div>
        </div>
        <div className="h-1.5 w-full bg-zinc-100 rounded-full" />
        <div className="mx-auto h-4 w-40 bg-zinc-100 rounded" />
      </div>

      {/* 테이블 스켈레톤 */}
      <div className="rounded-md border border-zinc-200 overflow-hidden">
        <div className="h-10 bg-zinc-50 border-b border-zinc-200" />
        {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
          <div
            key={i}
            className="flex gap-4 px-3 py-2 border-b border-zinc-100 last:border-0"
          >
            <div className="h-4 w-6 bg-zinc-200 rounded" />
            <div className="h-4 w-24 bg-zinc-100 rounded" />
            <div className="h-4 w-24 bg-zinc-100 rounded" />
            <div className="h-4 w-20 bg-zinc-200 rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}
