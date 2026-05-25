// Next.js 자동 로딩 UI — 스켈레톤 플레이스홀더
export default function PostgameLoading() {
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

      {/* 선수 목록 스켈레톤 */}
      <div className="grid gap-6 md:grid-cols-2">
        {[0, 1].map((i) => (
          <div key={i} className="border border-zinc-200 rounded-md">
            <div className="h-10 bg-zinc-50 border-b border-zinc-200 px-3 flex items-center">
              <div className="h-4 w-20 bg-zinc-200 rounded" />
            </div>
            {[0, 1, 2].map((j) => (
              <div
                key={j}
                className="flex justify-between px-3 py-2 border-b border-zinc-100 last:border-0"
              >
                <div className="space-y-1">
                  <div className="h-4 w-20 bg-zinc-200 rounded" />
                  <div className="h-3 w-32 bg-zinc-100 rounded" />
                </div>
                <div className="h-4 w-8 bg-zinc-200 rounded" />
              </div>
            ))}
          </div>
        ))}
      </div>

      {/* 선택 리뷰 스켈레톤 */}
      <div className="grid gap-3 md:grid-cols-2">
        {[0, 1].map((i) => (
          <div
            key={i}
            className="rounded-md border border-zinc-200 p-4 space-y-3"
          >
            <div className="flex justify-between">
              <div className="h-6 w-6 bg-zinc-200 rounded-full" />
              <div className="h-5 w-20 bg-zinc-200 rounded-full" />
            </div>
            <div className="h-4 w-40 bg-zinc-200 rounded" />
            <div className="h-3 w-full bg-zinc-100 rounded" />
            <div className="h-3 w-3/4 bg-zinc-100 rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}
