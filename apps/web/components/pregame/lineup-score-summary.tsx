import { ScoreCard } from "@/components/score-card";
import { StatusPill } from "@/components/status-pill";
import { VERDICT_KO, VERDICT_TONE } from "@/lib/i18n";
import type { PregameResponse } from "@/lib/types";

export function LineupScoreSummary({ pregame }: { pregame: PregameResponse }) {
  const gapTone =
    pregame.score_gap >= -0.02
      ? "good"
      : pregame.score_gap >= -0.05
        ? "neutral"
        : pregame.score_gap >= -0.1
          ? "warning"
          : "danger";

  const formatScore = (v: number) => v.toFixed(3);
  const formatGap = (v: number) => {
    const sign = v >= 0 ? "+" : "";
    return `${sign}${v.toFixed(3)}`;
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <ScoreCard
          label="실제 라인업 점수"
          value={formatScore(pregame.actual_score)}
          tone={gapTone}
        />
        <ScoreCard
          label="추천 라인업 점수"
          value={formatScore(pregame.recommended_score)}
          tone="neutral"
        />
        <ScoreCard
          label="차이"
          value={formatGap(pregame.score_gap)}
          helper="실제 - 추천"
          tone={gapTone}
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-zinc-500">판정:</span>
        <StatusPill tone={VERDICT_TONE[pregame.verdict]}>
          {VERDICT_KO[pregame.verdict]}
        </StatusPill>
      </div>
    </div>
  );
}
