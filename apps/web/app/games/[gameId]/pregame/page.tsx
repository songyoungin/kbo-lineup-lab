import { notFound } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import { LineupScoreSummary } from "@/components/pregame/lineup-score-summary";
import { LineupComparisonTable } from "@/components/pregame/lineup-comparison-table";
import { DifferenceCard } from "@/components/pregame/difference-card";
import { PlayerComparisonPanel } from "@/components/pregame/player-comparison-panel";

// 모델 한계 표시 컴포넌트
function ModelLimitations({ limitations }: { limitations: string[] }) {
  if (limitations.length === 0) return null;
  return (
    <section className="rounded-md border border-zinc-200 bg-zinc-50 p-4 space-y-2">
      <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide">
        모델 한계
      </h2>
      <ul className="list-disc list-inside space-y-1">
        {limitations.map((item, i) => (
          <li key={i} className="text-xs text-zinc-500">
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default async function PregamePage({
  params,
}: {
  params: Promise<{ gameId: string }>;
}) {
  const { gameId: rawId } = await params;
  const gameId = Number(rawId);
  if (!Number.isFinite(gameId)) notFound();

  let pregame: Awaited<ReturnType<typeof api.pregame>>;
  let comparison: Awaited<ReturnType<typeof api.lineupComparison>>;

  try {
    [pregame, comparison] = await Promise.all([
      api.pregame(gameId),
      api.lineupComparison(gameId),
    ]);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }

  // 비교 행에서 실제/추천 이름 맵 생성 (DifferenceCard에 전달용)
  const nameMap = new Map(
    comparison.rows.map((row) => [
      row.batting_order,
      {
        actualName: row.actual_player_name,
        recommendedName: row.recommended_player_name,
      },
    ])
  );

  return (
    <div className="space-y-6 max-w-4xl">
      {/* 헤더 */}
      <header>
        <h1 className="text-xl font-bold text-zinc-900">프리게임 평가</h1>
        <p className="text-sm text-zinc-500">Game #{gameId}</p>
      </header>

      {/* 점수 요약 */}
      <section>
        <h2 className="text-sm font-semibold text-zinc-700 uppercase tracking-wide mb-2">
          점수 요약
        </h2>
        <LineupScoreSummary pregame={pregame} />
      </section>

      {/* 라인업 비교 테이블 */}
      <section>
        <h2 className="text-sm font-semibold text-zinc-700 uppercase tracking-wide mb-2">
          라인업 비교
        </h2>
        <LineupComparisonTable rows={comparison.rows} />
      </section>

      {/* 주요 차이 카드 */}
      {pregame.differences.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-zinc-700 uppercase tracking-wide mb-2">
            주요 차이
          </h2>
          <div className="grid gap-3 md:grid-cols-2">
            {pregame.differences
              .filter((d) => d.difference_type !== "Same")
              .map((d) => {
                const names = nameMap.get(d.batting_order);
                return (
                  <DifferenceCard
                    key={d.batting_order}
                    difference={d}
                    actualName={names?.actualName}
                    recommendedName={names?.recommendedName}
                  />
                );
              })}
          </div>
        </section>
      )}

      {/* 선수 비교 패널 */}
      <section>
        <h2 className="text-sm font-semibold text-zinc-700 uppercase tracking-wide mb-2">
          선수 비교
        </h2>
        <PlayerComparisonPanel
          gameId={gameId}
          differences={pregame.differences}
        />
      </section>

      {/* 모델 한계 */}
      <ModelLimitations limitations={pregame.model_limitations} />
    </div>
  );
}
