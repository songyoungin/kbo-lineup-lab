import type { StatusTone } from "@/components/status-pill";
import type { Verdict } from "./types";

// Scale that maps the expected-run gap (|recommended − actual|) to a full gauge.
// Same intent as score-duel.tsx's GAP_FULL_SCALE (both represent 0.15 RE),
// but defined independently here to avoid coupling to that component's local constant.
export const VERSUS_FULL_SCALE = 0.15;

/**
 * AI-edge gauge fill ratio normalised to 0–1 (magnitude only, direction ignored).
 * score_gap = actual − recommended, so we take the absolute value.
 */
export function aiEdgeFraction(scoreGap: number): number {
  return Math.min(Math.abs(scoreGap) / VERSUS_FULL_SCALE, 1);
}

export interface DuelNarrative {
  resultKo: string;
  tone: StatusTone;
}

// Verdict narrative: the recommended lineup is an optimisation product, so its
// expected run value is always >= the actual lineup's. Rather than framing this as
// "who won", we use the existing verdict to convey how close the manager's lineup
// came to the AI optimum (smaller gap = manager performed better).
const _DUEL_BY_VERDICT: Record<Verdict, DuelNarrative> = {
  "Nearly optimal": { resultKo: "막상막하 — 감독 선전", tone: "good" },
  Acceptable: { resultKo: "감독 선방", tone: "good" },
  Questionable: { resultKo: "AI 우세", tone: "warning" },
  "Low offensive efficiency": { resultKo: "AI 판정승", tone: "danger" },
};

export function duelNarrative(verdict: Verdict): DuelNarrative {
  return _DUEL_BY_VERDICT[verdict];
}
