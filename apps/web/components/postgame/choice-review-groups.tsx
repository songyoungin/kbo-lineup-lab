import { StatusPill } from "@/components/status-pill";
import {
  POSTGAME_VERDICT_GROUPS,
  postgameRationaleKo,
  postgameVerdictGroupKey,
} from "@/lib/i18n";
import type { PostgameDifferenceReview } from "@/lib/types";

/**
 * Group the slot-level choice reviews by outcome
 * (실제 선택이 옳았음 / 비슷했음 / 추천이 나았음) instead of per-batter cards,
 * so the reader sees at a glance how the actual picks fared versus the model.
 */
export function ChoiceReviewGroups({
  reviews,
}: {
  reviews: PostgameDifferenceReview[];
}) {
  const groups = POSTGAME_VERDICT_GROUPS.map((group) => ({
    ...group,
    items: reviews.filter(
      (r) => postgameVerdictGroupKey(r.verdict) === group.key
    ),
  })).filter((group) => group.items.length > 0);

  return (
    <div className="grid gap-4 md:grid-cols-3">
      {groups.map((group) => (
        <div
          key={group.key}
          className="overflow-hidden rounded-xl border border-zinc-200/80 bg-surface shadow-sm"
        >
          <header className="flex items-center justify-between border-b border-zinc-100 px-4 py-2.5">
            <span className="text-sm font-semibold text-ink">
              {group.label}
            </span>
            <StatusPill tone={group.tone}>{group.items.length}</StatusPill>
          </header>
          <ul className="divide-y divide-zinc-100">
            {group.items.map((review) => (
              <li key={review.batting_order} className="space-y-1.5 px-4 py-3">
                <div className="flex items-center gap-2 text-sm">
                  <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-bold text-brand-700">
                    {review.batting_order}
                  </span>
                  <span className="font-medium text-zinc-800">
                    {review.actual_player_name}
                  </span>
                  <span className="text-xs text-zinc-400">→ 추천</span>
                  <span className="font-medium text-zinc-500">
                    {review.recommended_player_name}
                  </span>
                </div>
                <p className="text-xs leading-relaxed text-zinc-600">
                  {postgameRationaleKo(review)}
                </p>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
