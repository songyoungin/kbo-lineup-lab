import { notFound } from "next/navigation";
import Link from "next/link";
import { ApiError, api } from "@/lib/api";
import { VersusHero } from "@/components/pregame/versus-hero";
import { VersusRounds } from "@/components/pregame/versus-rounds";
import { LineupComparisonTable } from "@/components/pregame/lineup-comparison-table";
import { PlayerComparisonPanel } from "@/components/pregame/player-comparison-panel";
import { SectionHeading } from "@/components/section-heading";
import { LineupSimulator } from "@/components/pregame/lineup-simulator";
import { modelLimitationKo } from "@/lib/i18n";

// 모델 한계 표시 컴포넌트
function ModelLimitations({ limitations }: { limitations: string[] }) {
  if (limitations.length === 0) return null;
  return (
    <section className="space-y-2 rounded-md border border-rule bg-canvas p-4">
      <h2 className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-400">
        모델 한계
      </h2>
      <ul className="list-inside list-disc space-y-1">
        {limitations.map((item, i) => (
          <li key={i} className="text-xs text-zinc-500">
            {modelLimitationKo(item)}
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

  return (
    <div className="max-w-4xl space-y-8">
      {/* 헤더 */}
      <header className="reveal flex items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink">
            프리게임 평가
          </h1>
          <p className="mt-1.5 text-sm tabular-nums text-zinc-500">
            Game #{gameId}
          </p>
          {pregame.opponent_pitcher && (
            <p className="mt-1 text-sm tabular-nums text-zinc-500">
              상대 선발: ERA{" "}
              {pregame.opponent_pitcher.era != null
                ? pregame.opponent_pitcher.era.toFixed(2)
                : "-"}{" "}
              · WHIP{" "}
              {pregame.opponent_pitcher.whip != null
                ? pregame.opponent_pitcher.whip.toFixed(2)
                : "-"}{" "}
              · K%{" "}
              {pregame.opponent_pitcher.k_pct != null
                ? `${(pregame.opponent_pitcher.k_pct * 100).toFixed(0)}%`
                : "-"}
            </p>
          )}
        </div>
        <Link
          href={`/games/${gameId}/postgame`}
          className="inline-flex items-center gap-1 text-sm font-semibold text-brand-700 transition-colors hover:text-brand-800"
        >
          포스트게임 리뷰 보기 →
        </Link>
      </header>

      {/* AI vs 감독 대결 */}
      <section className="reveal reveal-1">
        <SectionHeading>AI vs 감독 대결</SectionHeading>
        <VersusHero pregame={pregame} />
      </section>

      {/* 라인업 비교 테이블 */}
      <section className="reveal reveal-2">
        <SectionHeading>라인업 비교</SectionHeading>
        <LineupComparisonTable rows={comparison.rows} />
      </section>

      {/* 대결 라운드 */}
      <section className="reveal reveal-3 space-y-3">
        <SectionHeading>대결 라운드</SectionHeading>
        <VersusRounds gameId={gameId} rows={comparison.rows} />
      </section>

      {/* 타순 시뮬레이터 */}
      <section className="reveal reveal-3 space-y-3">
        <SectionHeading>타순 시뮬레이터</SectionHeading>
        <LineupSimulator
          gameId={gameId}
          recommendedLineup={pregame.recommended_lineup}
        />
      </section>

      {/* 선수 비교 패널 */}
      <section className="reveal reveal-3">
        <SectionHeading>선수 비교</SectionHeading>
        <PlayerComparisonPanel
          gameId={gameId}
          differences={pregame.differences}
        />
      </section>

      {/* 모델 한계 */}
      <div className="reveal reveal-4">
        <ModelLimitations limitations={pregame.model_limitations} />
      </div>
    </div>
  );
}
