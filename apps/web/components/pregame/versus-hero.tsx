import { cn } from "@/lib/utils";
import { StatusPill } from "@/components/status-pill";
import { aiEdgeFraction, duelNarrative } from "@/lib/versus";
import type { PregameResponse } from "@/lib/types";

// 쐐기 최소 가시 폭 — 아주 작은 격차도 방향이 읽히도록.
const MIN_WEDGE_PCT = 1.5;

/**
 * "🤖 AI vs 🧢 감독" 대결 히어로 배너.
 * 양 코너에 추천/실제 라인업의 기대 득점을 마주 세우고, 중앙 게이지가 앞선 쪽으로
 * 뻗어 격차의 방향·크기를 보여준다. 추천은 최적화 산물이라 보통 AI가 앞서므로,
 * 판정 문구(duelNarrative)는 '감독이 얼마나 근접했나'를 verdict로 전한다.
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
      {/* 페르소나 대결 스코어라인 */}
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

      {/* 우세 게이지 — 중앙에서 앞선 쪽으로 쐐기가 뻗는다 */}
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

      {/* 판정 */}
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
