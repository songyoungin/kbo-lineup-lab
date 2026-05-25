import { cn } from "@/lib/utils";
import { StatusPill } from "@/components/status-pill";
import { VERDICT_KO, VERDICT_TONE } from "@/lib/i18n";
import type { PostgameDifferenceReview } from "@/lib/types";
import type { Verdict } from "@/lib/types";
import type { StatusTone } from "@/components/status-pill";

/** verdict 문자열을 StatusTone으로 변환합니다. */
function verdictToTone(verdict: string): StatusTone {
  // 표준 Verdict 리터럴이면 VERDICT_TONE 맵 사용
  const tone = VERDICT_TONE[verdict as Verdict];
  if (tone) return tone;

  // 자유 텍스트 verdict: 키워드 기반 유추
  const lower = verdict.toLowerCase();
  if (
    lower.includes("success") ||
    lower.includes("good") ||
    lower.includes("exceeded")
  )
    return "good";
  if (
    lower.includes("poor") ||
    lower.includes("under") ||
    lower.includes("failed")
  )
    return "danger";
  if (lower.includes("mixed") || lower.includes("neutral")) return "neutral";
  return "warning";
}

const BORDER_TONE: Record<StatusTone, string> = {
  neutral: "border-zinc-200",
  good: "border-emerald-200",
  warning: "border-amber-300",
  danger: "border-red-300",
};

/**
 * 실제 선택과 추천 선택이 달랐던 타순의 포스트게임 리뷰 카드.
 */
export function ChoiceReviewCard({
  review,
}: {
  review: PostgameDifferenceReview;
}) {
  const tone = verdictToTone(review.verdict);
  const verdictDisplay =
    VERDICT_KO[review.verdict as Verdict] ?? review.verdict;

  return (
    <div
      className={cn(
        "rounded-md border bg-white p-4 space-y-3",
        BORDER_TONE[tone]
      )}
    >
      {/* 상단: 타순 배지 + verdict 필 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-zinc-100 text-xs font-bold text-zinc-600">
            {review.batting_order}
          </span>
          <span className="text-xs text-zinc-400">번타자</span>
        </div>
        <StatusPill tone={tone}>{verdictDisplay}</StatusPill>
      </div>

      {/* 선수 비교: 실제 → 추천 */}
      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium text-zinc-800">
          {review.actual_player_name}
        </span>
        <span className="text-zinc-400 text-xs">→ 추천:</span>
        <span className="font-medium text-zinc-500">
          {review.recommended_player_name}
        </span>
      </div>

      {/* 실제 성적 */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-zinc-500">실제 성적:</span>
        <span className="text-sm font-mono font-semibold text-zinc-800">
          {review.actual_performance.toFixed(1)}
        </span>
      </div>

      {/* 사유 */}
      <p className="text-xs text-zinc-600 leading-relaxed">
        {review.rationale}
      </p>
    </div>
  );
}
