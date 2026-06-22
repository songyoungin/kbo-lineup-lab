import { cn } from "@/lib/utils";
import { StatusPill } from "@/components/status-pill";
import { aiEdgeFraction, duelNarrative } from "@/lib/versus";
import type { PregameResponse } from "@/lib/types";

// Minimum visible wedge width — ensures direction is readable even for tiny gaps.
const MIN_WEDGE_PCT = 1.5;

/**
 * "🤖 AI vs 🧢 감독" duel hero banner.
 * Displays the expected run values of the recommended and actual lineups in opposite
 * corners; a central gauge extends toward the leading side to show the gap's
 * direction and magnitude. Since the recommended lineup is an optimisation product
 * the AI typically leads, so the verdict label (duelNarrative) frames the result as
 * "how close did the manager get" rather than a raw win/loss.
 */
export function VersusHero({ pregame }: { pregame: PregameResponse }) {
  const aiLeads = pregame.recommended_score > pregame.actual_score;
  const tie = pregame.recommended_score === pregame.actual_score;
  const wedgePct = tie
    ? 0
    : Math.max(aiEdgeFraction(pregame.score_gap) * 50, MIN_WEDGE_PCT);
  const narrative = duelNarrative(pregame.verdict);
  const sign = pregame.score_gap >= 0 ? "+" : "";

  return (
    <div className="rounded-md border border-rule bg-surface px-6 py-6">
      {/* Persona duel scoreline */}
      <div className="flex items-stretch justify-between gap-4">
        <div className="flex-1">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-brand-600">
            🤖 AI · 추천 라인업
          </p>
          <p className="mt-1 text-4xl font-bold tabular-nums text-ink">
            {pregame.recommended_score.toFixed(3)}
          </p>
          <p className="mt-0.5 text-[11px] text-zinc-400">기대 득점</p>
        </div>
        <span
          aria-hidden
          className="self-center text-base font-black italic text-zinc-300"
        >
          VS
        </span>
        <div className="flex-1 text-right">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-500">
            🧢 감독 · 실제 라인업
          </p>
          <p className="mt-1 text-4xl font-bold tabular-nums text-ink">
            {pregame.actual_score.toFixed(3)}
          </p>
          <p className="mt-0.5 text-[11px] text-zinc-400">기대 득점</p>
        </div>
      </div>

      {/* Edge gauge — wedge extends from centre toward the leading side */}
      <div className="relative mt-5 h-2 rounded-full bg-rule/50">
        <span
          aria-hidden
          className="absolute left-1/2 top-1/2 h-3.5 w-px -translate-x-1/2 -translate-y-1/2 bg-zinc-400"
        />
        <span
          className={cn(
            "absolute top-0 h-full rounded-full",
            aiLeads ? "bg-brand-600" : "bg-zinc-400"
          )}
          style={
            aiLeads
              ? { right: "50%", width: `${wedgePct}%` }
              : { left: "50%", width: `${wedgePct}%` }
          }
          aria-label={`AI 우세 게이지 ${wedgePct.toFixed(0)}%`}
        />
      </div>

      {/* Verdict */}
      <div className="mt-4 flex items-center justify-center gap-2.5">
        <span className="text-sm font-semibold tabular-nums text-zinc-600">
          Δ {sign}
          {pregame.score_gap.toFixed(3)}
        </span>
        <span className="text-[11px] text-zinc-400">실제 − 추천</span>
        <StatusPill tone={narrative.tone}>{narrative.resultKo}</StatusPill>
      </div>
    </div>
  );
}
