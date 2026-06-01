// 표시 레이어용 한국어 번역 맵
// JSON enum 값은 영문으로 유지하고, UI 표시 시 이 맵을 사용합니다.

import type { StatusTone } from "@/components/status-pill";
import type {
  AdminCategoryStatus,
  DifferenceType,
  PerformanceLabel,
  PostgameDifferenceReview,
  PregameGapLabel,
  Verdict,
} from "@/lib/types";

// KBO 팀 코드 → 한국어 표시명. DB의 team.name은 영문이므로 표시 레이어에서 변환합니다.
export const TEAM_NAME_KO: Record<string, string> = {
  LG: "LG 트윈스",
  OB: "두산 베어스",
  WO: "키움 히어로즈",
  SK: "SSG 랜더스",
  HT: "KIA 타이거즈",
  SS: "삼성 라이온즈",
  LT: "롯데 자이언츠",
  HH: "한화 이글스",
  NC: "NC 다이노스",
  KT: "KT 위즈",
};

/** 팀 코드를 한국어 표시명으로 변환합니다. 미등록 코드는 코드를 그대로 반환합니다. */
export function teamNameKo(code: string): string {
  return TEAM_NAME_KO[code] ?? code;
}

// 팀 홈 pipeline_status 의 단축 키 → 한국어 라벨 (어드민 CATEGORY_KO 와 별개의 단축 키 집합).
export const HOME_PIPELINE_LABEL_KO: Record<string, string> = {
  schedule: "스케줄",
  lineup: "라인업",
  eval: "프리게임 평가",
  box: "박스스코어",
  postgame: "포스트게임 리뷰",
};

export const VERDICT_KO: Record<Verdict, string> = {
  "Nearly optimal": "거의 최적",
  Acceptable: "수용 가능",
  Questionable: "의문스러움",
  "Low offensive efficiency": "낮은 공격 효율",
};

export const DIFF_TYPE_KO: Record<DifferenceType, string> = {
  Same: "동일",
  "Player changed": "선수 변경",
  "Position changed": "포지션 변경",
  "Batting order changed": "타순 변경",
  "Player and order changed": "선수+타순 변경",
};

export const VERDICT_TONE: Record<Verdict, StatusTone> = {
  "Nearly optimal": "good",
  Acceptable: "good",
  Questionable: "warning",
  "Low offensive efficiency": "danger",
};

export const DIFF_TONE: Record<DifferenceType, StatusTone> = {
  Same: "neutral",
  "Player changed": "warning",
  "Position changed": "warning",
  "Batting order changed": "warning",
  "Player and order changed": "danger",
};

export const PERFORMANCE_LABEL_KO: Record<PerformanceLabel, string> = {
  Overperformed: "기대 이상",
  Expected: "기대치",
  Underperformed: "기대 이하",
};

export const PERFORMANCE_TONE: Record<PerformanceLabel, StatusTone> = {
  Overperformed: "good",
  Expected: "neutral",
  Underperformed: "danger",
};

// ---------------------------------------------------------------------------
// 어드민 수집 상태
// ---------------------------------------------------------------------------

export const ADMIN_STATUS_KO: Record<AdminCategoryStatus, string> = {
  waiting: "대기",
  collected: "수집됨",
  normalized: "정규화됨",
  complete: "완료",
  failed: "실패",
  needs_review: "검토 필요",
};

export const ADMIN_STATUS_TONE: Record<AdminCategoryStatus, StatusTone> = {
  waiting: "neutral",
  collected: "neutral",
  normalized: "good",
  complete: "good",
  failed: "danger",
  needs_review: "warning",
};

export const CATEGORY_KO: Record<string, string> = {
  schedule: "스케줄",
  roster: "로스터",
  player_stats: "선수 스탯",
  stat_snapshot: "스탯 스냅샷",
  lineup: "라인업",
  evaluation: "프리게임 평가",
  box_score: "박스스코어",
  postgame_review: "포스트게임 리뷰",
};

// 백엔드 _pick_gap_label은 소문자 리터럴을 반환합니다 (VERDICT와 별개 맵).
export const GAP_LABEL_KO: Record<PregameGapLabel, string> = {
  "nearly optimal": "거의 최적",
  acceptable: "수용 가능",
  questionable: "의문스러움",
  "low offensive efficiency": "낮은 공격 효율",
};

export const GAP_LABEL_TONE: Record<PregameGapLabel, StatusTone> = {
  "nearly optimal": "good",
  acceptable: "neutral",
  questionable: "warning",
  "low offensive efficiency": "danger",
};

// ---------------------------------------------------------------------------
// 포스트게임 선택 리뷰 — verdict 그룹 (영문 verdict는 백엔드 로직 키이므로 유지)
// ---------------------------------------------------------------------------

export interface PostgameVerdictGroup {
  key: string;
  label: string;
  tone: StatusTone;
}

// 표시 순서대로. "Inconclusive …"(드묾)는 마지막 판단 보류 그룹으로.
export const POSTGAME_VERDICT_GROUPS: PostgameVerdictGroup[] = [
  { key: "actual", label: "실제 선택이 옳았음", tone: "good" },
  { key: "comparable", label: "비슷했음", tone: "neutral" },
  { key: "model", label: "추천이 나았음", tone: "warning" },
  { key: "inconclusive", label: "판단 보류", tone: "neutral" },
];

/** verdict 문자열을 그룹 key로. 미등록 verdict는 판단 보류로 분류. */
export function postgameVerdictGroupKey(verdict: string): string {
  if (verdict === "Actual choice succeeded") return "actual";
  if (verdict === "Both comparable") return "comparable";
  if (verdict === "Model would have done better") return "model";
  return "inconclusive";
}

/** 구조화 필드로 한국어 사유 문장을 조립한다(영문 rationale 대체). */
export function postgameRationaleKo(r: PostgameDifferenceReview): string {
  const actual = r.actual_player_name;
  const rec = r.recommended_player_name;
  const a = r.actual_performance.toFixed(1);
  if (r.recommended_performance == null) {
    return `${actual} ${a}점 · 추천 ${rec}는 박스스코어에 출전 기록이 없습니다.`;
  }
  const b = r.recommended_performance.toFixed(1);
  switch (postgameVerdictGroupKey(r.verdict)) {
    case "actual":
      return `실제 ${actual}가 추천 ${rec}보다 좋았습니다 (${a} vs ${b}점).`;
    case "model":
      return `추천 ${rec}가 더 나았을 것입니다 (${b} vs 실제 ${actual} ${a}점).`;
    case "comparable":
      return `${actual}와 ${rec}의 성과가 비슷했습니다 (${a} vs ${b}점).`;
    default:
      return `${actual} ${a}점 / 추천 ${rec} ${b}점.`;
  }
}

// ---------------------------------------------------------------------------
// 미반영 요소 / 모델 한계 / 종합 평가 — 백엔드의 고정 영문 문자열을 한글로
// ---------------------------------------------------------------------------

const UNMODELED_FACTOR_KO: Record<string, string> = {
  "Defense and baserunning value": "수비·주루 가치",
  "Injury / fatigue / rest days": "부상 / 피로 / 휴식일",
  "Manager matchup tendencies": "감독의 매치업 성향",
  "Platoon or late-inning substitution plans": "플래툰 / 후반 교체 계획",
};

/** 미반영 요소 문자열을 한글로. 미등록 문자열은 원문 유지. */
export function unmodeledFactorKo(factor: string): string {
  return UNMODELED_FACTOR_KO[factor] ?? factor;
}

const MODEL_LIMITATION_KO: Record<string, string> = {
  "Defaulted to RIGHT for MVP; derive from actual starter data in future.":
    "상대 선발 손잡이를 우투로 가정했습니다(향후 실제 선발 데이터로 대체 예정).",
  "Actual lineup score is computed by feeding the announced lineup through compute_lineup_score with each slot's position synthesised into the player's secondary_positions if not already present, so every slot is scoreable. This keeps the actual and recommended scores on the same scale.":
    "실제 라인업 점수는 발표 라인업을 compute_lineup_score로 계산하며, 각 슬롯의 포지션을 선수의 보조 포지션에 합성해 모든 슬롯을 채점 가능하게 합니다. 이로써 실제·추천 점수가 동일 척도를 유지합니다.",
  "Performance score uses box score totals only; does not account for context (RISP, leverage, etc.)":
    "성과 점수는 박스스코어 합계만 사용하며, 맥락(득점권·레버리지 등)은 반영하지 않습니다.",
};

/** 모델 한계 문자열을 한글로. 동적 패턴(상대 손잡이 기본값)도 처리하고, 미등록은 원문 유지. */
export function modelLimitationKo(limitation: string): string {
  const exact = MODEL_LIMITATION_KO[limitation];
  if (exact) return exact;
  const handed = limitation.match(/^Opponent handedness defaulted to (\w+)$/);
  if (handed) {
    const hand = handed[1].toUpperCase().startsWith("L") ? "좌투" : "우투";
    return `상대 손잡이를 ${hand}로 가정했습니다.`;
  }
  return limitation;
}

const SUMMARY_TEXT_KO: Record<string, string> = {
  "The actual lineup was weaker than the recommendation, but the selected players exceeded expectations.":
    "실제 라인업은 추천보다 약했지만, 선택된 선수들이 기대를 뛰어넘었습니다.",
  "The model disliked the choice before the game, and the result also underperformed.":
    "모델은 경기 전 이 선택을 낮게 봤고, 결과도 기대에 못 미쳤습니다.",
  "The actual choice differed from the model and succeeded.":
    "실제 선택이 모델과 달랐지만 성공적이었습니다.",
  "The actual lineup was close to optimal and performed within expectation.":
    "실제 라인업은 최적에 가까웠고, 기대 범위 내에서 경기했습니다.",
};

/** 종합 평가 문장을 한글로. 미등록 문자열은 원문 유지. */
export function summaryTextKo(text: string): string {
  return SUMMARY_TEXT_KO[text] ?? text;
}
