import { ScoreDuel } from "@/components/score-duel";
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

  return (
    <ScoreDuel
      actualScore={pregame.actual_score}
      recommendedScore={pregame.recommended_score}
      gap={pregame.score_gap}
      gapTone={gapTone}
      verdictLabel={VERDICT_KO[pregame.verdict]}
      verdictTone={VERDICT_TONE[pregame.verdict]}
    />
  );
}
