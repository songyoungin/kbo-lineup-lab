import { ScoreDuel } from "@/components/score-duel";
import { GAP_LABEL_KO, GAP_LABEL_TONE } from "@/lib/i18n";
import type { PostgameResponse } from "@/lib/types";

/**
 * 포스트게임 결과 요약 — 실제/추천 점수를 마주 보는 스코어라인으로 표시.
 * pregame_actual_score, pregame_recommended_score, pregame_score_gap,
 * pregame_gap_label 값을 ScoreDuel 한 장으로 보여줍니다.
 */
export function ResultSummary({ review }: { review: PostgameResponse }) {
  const gap = review.pregame_score_gap;
  const gapTone =
    gap >= -0.02
      ? "good"
      : gap >= -0.05
        ? "neutral"
        : gap >= -0.1
          ? "warning"
          : "danger";

  // 백엔드 _pick_gap_label은 소문자 리터럴을 반환하므로 GAP_LABEL_* 맵을 사용.
  const labelTone = GAP_LABEL_TONE[review.pregame_gap_label] ?? gapTone;
  const labelText =
    GAP_LABEL_KO[review.pregame_gap_label] ?? review.pregame_gap_label;

  return (
    <ScoreDuel
      actualScore={review.pregame_actual_score}
      recommendedScore={review.pregame_recommended_score}
      gap={gap}
      gapTone={gapTone}
      verdictLabel={labelText}
      verdictTone={labelTone}
    />
  );
}
