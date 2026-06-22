import type { StatusTone } from "@/components/status-pill";
import type { Verdict } from "./types";

// 기대 득점 격차(추천 − 실제의 절댓값)를 게이지 가득참으로 매핑하는 스케일.
// score-duel.tsx의 GAP_FULL_SCALE와 같은 의도의 상수(둘 다 0.15 RE)지만,
// 그 컴포넌트의 로컬 상수와의 결합을 피하려고 여기서 독립적으로 정의한다.
export const VERSUS_FULL_SCALE = 0.15;

/**
 * 0–1로 정규화한 AI 우세 게이지 채움 비율(격차의 크기만, 방향은 무시).
 * score_gap = 실제 − 추천 이므로 절댓값을 쓴다.
 */
export function aiEdgeFraction(scoreGap: number): number {
  return Math.min(Math.abs(scoreGap) / VERSUS_FULL_SCALE, 1);
}

export interface DuelNarrative {
  resultKo: string;
  tone: StatusTone;
}

// 판정 서사: 추천 라인업은 최적화 산물이라 기대 득점이 항상 실제 이상이다.
// 따라서 '누가 이겼나'가 아니라 감독의 라인업이 AI 최적안에 얼마나 근접했는지를
// 기존 verdict로 전한다(격차가 작을수록 감독 선전).
const _DUEL_BY_VERDICT: Record<Verdict, DuelNarrative> = {
  "Nearly optimal": { resultKo: "막상막하 — 감독 선전", tone: "good" },
  Acceptable: { resultKo: "감독 선방", tone: "good" },
  Questionable: { resultKo: "AI 우세", tone: "warning" },
  "Low offensive efficiency": { resultKo: "AI 판정승", tone: "danger" },
};

export function duelNarrative(verdict: Verdict): DuelNarrative {
  return _DUEL_BY_VERDICT[verdict];
}
