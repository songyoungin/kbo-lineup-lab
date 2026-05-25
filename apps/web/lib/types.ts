// TypeScript types mirroring the Python Pydantic response schemas.
// Field names use snake_case to match the API JSON output directly.

// ---------------------------------------------------------------------------
// Shared literals
// ---------------------------------------------------------------------------

export type Verdict =
  | "Nearly optimal"
  | "Acceptable"
  | "Questionable"
  | "Low offensive efficiency";

export type DifferenceType =
  | "Same"
  | "Player changed"
  | "Position changed"
  | "Batting order changed"
  | "Player and order changed";

export type PerformanceLabel = "Overperformed" | "Expected" | "Underperformed";

// 백엔드 _pick_gap_label은 소문자 리터럴 4종을 반환합니다.
// VERDICT와 의미는 동일하나 케이스가 달라 별도 타입으로 분리.
export type PregameGapLabel =
  | "nearly optimal"
  | "acceptable"
  | "questionable"
  | "low offensive efficiency";

// ---------------------------------------------------------------------------
// Team home
// ---------------------------------------------------------------------------

export interface RecentGameSummary {
  game_id: number;
  game_date: string; // ISO date string "YYYY-MM-DD"
  opponent_team_code: string;
  verdict: Verdict | null;
}

export interface TeamHomeGameCard {
  game_id: number;
  game_date: string; // ISO date string "YYYY-MM-DD"
  opponent_team_code: string;
  venue: string | null;
  opponent_starter: string | null;
  pipeline_status: Record<string, string>;
}

export interface TeamHomeResponse {
  team_code: string;
  today: TeamHomeGameCard | null;
  recent: RecentGameSummary[];
}

// ---------------------------------------------------------------------------
// Pregame evaluation
// ---------------------------------------------------------------------------

export interface LineupRow {
  batting_order: number;
  position: string;
  player_id: number;
  player_name: string;
}

export interface LineupDifference {
  batting_order: number;
  difference_type: DifferenceType;
  main_reason: string;
}

export interface PregameResponse {
  game_id: number;
  actual_score: number;
  recommended_score: number;
  score_gap: number;
  verdict: Verdict;
  actual_lineup: LineupRow[];
  recommended_lineup: LineupRow[];
  differences: LineupDifference[];
  model_limitations: string[];
}

// ---------------------------------------------------------------------------
// Lineup comparison
// ---------------------------------------------------------------------------

export interface LineupComparisonRow {
  batting_order: number;
  actual_player_id: number;
  actual_player_name: string;
  actual_position: string;
  recommended_player_id: number;
  recommended_player_name: string;
  recommended_position: string;
  difference_type: DifferenceType;
  main_reason: string;
}

export interface LineupComparisonResponse {
  game_id: number;
  rows: LineupComparisonRow[];
}

// ---------------------------------------------------------------------------
// Player comparison
// ---------------------------------------------------------------------------

export interface PlayerComparisonStats {
  player_id: number;
  player_name: string;
  position: string;
  ops: number;
  obp: number;
  slg: number;
  recent_14d_ops: number | null;
  recent_30d_ops: number | null;
  vs_rhp_ops: number | null;
  vs_lhp_ops: number | null;
  pa_vs_rhp: number;
  pa_vs_lhp: number;
  starts_last_5: number;
  model_score: number | null;
}

export interface PlayerComparisonResponse {
  batting_order: number;
  actual: PlayerComparisonStats;
  recommended: PlayerComparisonStats;
  judgment: string;
  unmodeled_factors: string[];
}

// ---------------------------------------------------------------------------
// Postgame review
// ---------------------------------------------------------------------------

export interface PostgamePlayerLine {
  player_id: number;
  name: string;
  performance_score: number;
  label: PerformanceLabel;
  box_line: Record<string, unknown>;
}

export interface PostgameDifferenceReview {
  batting_order: number;
  actual_player_id: number;
  actual_player_name: string;
  recommended_player_id: number;
  recommended_player_name: string;
  actual_performance: number;
  verdict: string;
  rationale: string;
}

export interface PostgameResponse {
  game_id: number;
  evaluation_run_id: number;
  postgame_review_run_id: number;
  pregame_actual_score: number;
  pregame_recommended_score: number;
  pregame_score_gap: number;
  pregame_gap_label: PregameGapLabel;
  overperformers: PostgamePlayerLine[];
  underperformers: PostgamePlayerLine[];
  other_actual: PostgamePlayerLine[];
  difference_reviews: PostgameDifferenceReview[];
  summary_text: string;
  model_limitations: string[];
}

// ---------------------------------------------------------------------------
// Job requests / responses
// ---------------------------------------------------------------------------

export interface ReplayEvaluationRequest {
  game_id: number;
  team_id: number;
  evaluation_cutoff_at: string; // ISO datetime string with timezone
  model_version_id: number;
}

export interface ReplayEvaluationResponse {
  evaluation_run_id: number;
  created: boolean;
  status: string;
}

export interface GeneratePostgameReviewRequest {
  evaluation_run_id: number;
  box_score_snapshot_id: number;
}

export interface GeneratePostgameReviewResponse {
  postgame_review_run_id: number;
  created: boolean;
  status: string;
}
