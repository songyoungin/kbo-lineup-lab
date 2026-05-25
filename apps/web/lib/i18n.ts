// 표시 레이어용 한국어 번역 맵
// JSON enum 값은 영문으로 유지하고, UI 표시 시 이 맵을 사용합니다.

import type { StatusTone } from "@/components/status-pill";
import type { DifferenceType, Verdict } from "@/lib/types";

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
