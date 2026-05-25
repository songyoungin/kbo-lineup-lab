import { ScoreCard } from "@/components/score-card";
import { StatusPill } from "@/components/status-pill";
import { GAP_LABEL_KO, GAP_LABEL_TONE } from "@/lib/i18n";
import type { PostgameResponse } from "@/lib/types";

/**
 * 포스트게임 결과 요약 카드 섹션.
 * pregame_actual_score, pregame_recommended_score, pregame_score_gap,
 * pregame_gap_label 값을 4-카드 그리드로 표시합니다.
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

  const formatScore = (v: number) => v.toFixed(3);
  const formatGap = (v: number) => {
    const sign = v >= 0 ? "+" : "";
    return `${sign}${v.toFixed(3)}`;
  };

  // 백엔드 _pick_gap_label은 소문자 리터럴을 반환하므로 GAP_LABEL_* 맵을 사용.
  const labelTone = GAP_LABEL_TONE[review.pregame_gap_label] ?? gapTone;
  const labelText =
    GAP_LABEL_KO[review.pregame_gap_label] ?? review.pregame_gap_label;

  return (
    <section className="space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <ScoreCard
          label="실제 라인업 점수"
          value={formatScore(review.pregame_actual_score)}
          tone={gapTone}
        />
        <ScoreCard
          label="추천 라인업 점수"
          value={formatScore(review.pregame_recommended_score)}
          tone="neutral"
        />
        <ScoreCard
          label="점수 차이"
          value={formatGap(gap)}
          helper="실제 − 추천"
          tone={gapTone}
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-zinc-500">판정:</span>
        <StatusPill tone={labelTone}>{labelText}</StatusPill>
      </div>
    </section>
  );
}
