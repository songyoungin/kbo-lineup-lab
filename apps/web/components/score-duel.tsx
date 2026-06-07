import { cn } from "@/lib/utils";
import { StatusPill, type StatusTone } from "@/components/status-pill";

const WEDGE_CLASSES: Record<StatusTone, string> = {
  neutral: "bg-zinc-400",
  good: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-rose-500",
  brand: "bg-brand-600",
};

/** 델타 바 최대 스케일 — |gap|이 이 값 이상이면 바가 절반 끝까지 찬다. */
const GAP_FULL_SCALE = 0.15;

/**
 * "실제 ↔ 추천" 대결 스코어라인.
 * 양쪽 점수를 마주 보게 배치하고, 중앙 델타 바가 우세한 쪽으로 뻗어
 * 점수 차이(gap = 실제 − 추천)의 방향과 크기를 보여줍니다.
 */
export function ScoreDuel({
  actualScore,
  recommendedScore,
  gap,
  gapTone,
  verdictLabel,
  verdictTone,
}: {
  actualScore: number;
  recommendedScore: number;
  gap: number;
  gapTone: StatusTone;
  verdictLabel: string;
  verdictTone: StatusTone;
}) {
  const fraction = Math.min(Math.abs(gap) / GAP_FULL_SCALE, 1);
  // 0이 아닌 차이는 최소 1.5%로 보이게 (아주 작은 격차도 방향은 읽히도록)
  const wedgePct = gap === 0 ? 0 : Math.max(fraction * 50, 1.5);
  const actualLeads = gap > 0;
  const sign = gap >= 0 ? "+" : "";

  return (
    <div className="rounded-md border border-rule bg-surface px-6 py-5">
      {/* Facing scoreline */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-500">
            실제 라인업
          </p>
          <p className="mt-1 text-4xl font-bold tabular-nums text-ink">
            {actualScore.toFixed(3)}
          </p>
        </div>
        <span
          aria-hidden
          className="pb-1.5 text-sm font-semibold italic text-zinc-300"
        >
          vs
        </span>
        <div className="text-right">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-500">
            추천 라인업
          </p>
          <p className="mt-1 text-4xl font-bold tabular-nums text-ink">
            {recommendedScore.toFixed(3)}
          </p>
        </div>
      </div>

      {/* Delta bar — wedge grows from the centre toward the leading side */}
      <div className="relative mt-4 h-1.5 rounded-full bg-rule/50">
        <span
          aria-hidden
          className="absolute left-1/2 top-1/2 h-3 w-px -translate-x-1/2 -translate-y-1/2 bg-zinc-400"
        />
        <span
          className={cn(
            "absolute top-0 h-full rounded-full",
            WEDGE_CLASSES[gapTone]
          )}
          style={
            actualLeads
              ? { right: "50%", width: `${wedgePct}%` }
              : { left: "50%", width: `${wedgePct}%` }
          }
        />
      </div>

      {/* Delta value + verdict */}
      <div className="mt-3 flex items-center justify-center gap-2.5">
        <span className="text-sm font-semibold tabular-nums text-zinc-600">
          Δ {sign}
          {gap.toFixed(3)}
        </span>
        <span className="text-[11px] text-zinc-400">실제 − 추천</span>
        <StatusPill tone={verdictTone}>{verdictLabel}</StatusPill>
      </div>
    </div>
  );
}
