// Next.js 자동 로딩 UI — 스켈레톤 플레이스홀더
export default function PregameLoading() {
  return (
    <div className="space-y-6 max-w-4xl animate-pulse">
      {/* 헤더 스켈레톤 */}
      <div className="space-y-2">
        <div className="h-6 w-48 bg-zinc-200 rounded" />
        <div className="h-4 w-24 bg-zinc-100 rounded" />
      </div>

      {/* 점수 카드 스켈레톤 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="rounded-md border border-zinc-200 p-4 space-y-2"
          >
            <div className="h-3 w-24 bg-zinc-200 rounded" />
            <div className="h-8 w-16 bg-zinc-200 rounded" />
          </div>
        ))}
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
