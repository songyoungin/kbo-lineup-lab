// 표시 레이어용 한국어 번역 맵
// JSON enum 값은 영문으로 유지하고, UI 표시 시 이 맵을 사용합니다.

import type { StatusTone } from "@/components/status-pill";
import type {
  AdminCategoryStatus,
  DifferenceType,
  PerformanceLabel,
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
