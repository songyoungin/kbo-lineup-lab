import Link from "next/link";
import { ApiError, api } from "@/lib/api";
import { MOCK_TEAM_HOME } from "@/lib/mock";
import type { TeamHomeGameCard, TeamHomeResponse } from "@/lib/types";
import { ScoreCard } from "@/components/score-card";
import { StatusPill } from "@/components/status-pill";
import { DataTable, type Column } from "@/components/data-table";
import type { StatusTone } from "@/components/status-pill";
import type { AdminCategoryStatus, RecentGameSummary } from "@/lib/types";
import {
  ADMIN_STATUS_KO,
  HOME_PIPELINE_LABEL_KO,
  teamNameKo,
  VERDICT_KO,
  VERDICT_TONE,
} from "@/lib/i18n";

/** pipeline_status 값을 한국어로. 미등록 값(레거시 mock 등)은 원문을 반환. */
function pipelineStatusKo(status: string): string {
  return ADMIN_STATUS_KO[status as AdminCategoryStatus] ?? status;
}

/** True once the game has a final score. */
function isPlayed(t: TeamHomeGameCard): boolean {
  return (
    t.status === "RESULT" && t.team_score != null && t.opponent_score != null
  );
}

/** "승"/"패"/"무" from LG's perspective, or null when not yet played. */
function resultTag(
  t: TeamHomeGameCard
): { label: string; won: boolean } | null {
  if (!isPlayed(t)) return null;
  const team = t.team_score as number;
  const opp = t.opponent_score as number;
  if (team > opp) return { label: "승", won: true };
  if (team < opp) return { label: "패", won: false };
  return { label: "무", won: false };
}

/** "승리 X · 패전 Y · 세이브 Z" line, or null when no decisions are known. */
function pitcherLine(t: TeamHomeGameCard): string | null {
  const parts: string[] = [];
  if (t.winning_pitcher_name) parts.push(`승리 ${t.winning_pitcher_name}`);
  if (t.losing_pitcher_name) parts.push(`패전 ${t.losing_pitcher_name}`);
  if (t.save_pitcher_name) parts.push(`세이브 ${t.save_pitcher_name}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

// Map pipeline status value → tone.
// Canonical ingestion vocabulary: waiting | collected | normalized | complete | failed | needs_review.
// Legacy/mock values (ok | missing | pending | error) are kept for backward compatibility.
function pipelineTone(status: string): StatusTone {
  if (status === "ok" || status === "complete" || status === "normalized")
    return "good";
  if (status === "missing" || status === "error" || status === "failed")
    return "danger";
  if (
    status === "pending" ||
    status === "collected" ||
    status === "needs_review"
  )
    return "warning";
  return "neutral"; // includes "waiting"
}

const RECENT_COLUMNS: Column<RecentGameSummary>[] = [
  {
    header: "날짜",
    accessor: (row) => row.game_date,
  },
  {
    header: "상대",
    accessor: (row) => teamNameKo(row.opponent_team_code),
  },
  {
    header: "평가",
    accessor: (row) =>
      row.verdict ? (
        <StatusPill tone={VERDICT_TONE[row.verdict]}>
          {VERDICT_KO[row.verdict]}
        </StatusPill>
      ) : (
        <span className="text-xs text-zinc-400">-</span>
      ),
  },
];

export default async function TeamHomePage() {
  let home: TeamHomeResponse;
  let usingMock = false;

  try {
    home = await api.teamHome();
  } catch (e) {
    if (e instanceof ApiError) {
      home = MOCK_TEAM_HOME;
      usingMock = true;
    } else {
      throw e;
    }
  }

  const { today, recent } = home;

  return (
    <div className="max-w-4xl space-y-8">
      {/* Mock fallback notice */}
      {usingMock && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs font-medium text-amber-700">
          API에 연결할 수 없어 픽스처 형태의 모의 데이터를 표시하고 있습니다.
        </div>
      )}

      {/* Page header */}
      <div className="flex items-center gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-ink">
            LG 트윈스 팀 홈
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            라인업 평가와 파이프라인 현황을 한눈에
          </p>
        </div>
        <span className="ml-auto rounded-full bg-brand-50 px-3 py-1 text-xs font-bold tracking-wide text-brand-700 ring-1 ring-inset ring-brand-200">
          {home.team_code}
        </span>
      </div>

      {/* Today's game hero card */}
      {today ? (
        <section className="space-y-4">
          <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-400">
            오늘의 경기
          </h2>
          <div className="overflow-hidden rounded-2xl border border-zinc-200/80 bg-surface shadow-sm">
            {/* Crimson matchup banner */}
            <div className="flex items-center justify-between gap-4 bg-gradient-to-r from-brand-700 to-brand-600 px-6 py-5 text-white">
              <div>
                <div className="flex items-center gap-2.5">
                  <p className="text-2xl font-extrabold tracking-tight">
                    {teamNameKo(home.team_code)}{" "}
                    {isPlayed(today) ? (
                      <span className="tabular-nums">
                        {today.team_score}
                        <span className="px-1.5 text-brand-200">:</span>
                        {today.opponent_score}
                      </span>
                    ) : (
                      <span className="text-brand-200">vs</span>
                    )}{" "}
                    {teamNameKo(today.opponent_team_code)}
                  </p>
                  {resultTag(today) && (
                    <span
                      className={`rounded-md px-2 py-0.5 text-sm font-bold ${
                        resultTag(today)!.won
                          ? "bg-white text-brand-700"
                          : "bg-white/20 text-white"
                      }`}
                    >
                      {resultTag(today)!.label}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs font-medium text-brand-100">
                  {today.game_date}
                  {today.venue && ` · ${today.venue}`}
                  {today.opponent_starter &&
                    ` · 상대 선발: ${today.opponent_starter}`}
                </p>
                {pitcherLine(today) && (
                  <p className="mt-1 text-xs font-medium text-brand-100/90">
                    {pitcherLine(today)}
                  </p>
                )}
              </div>
              <span className="shrink-0 self-start rounded-full bg-white/15 px-3 py-1 text-xs font-semibold backdrop-blur">
                Game #{today.game_id}
              </span>
            </div>

            {/* Actions */}
            <div className="flex flex-wrap gap-2.5 px-6 py-4">
              <Link
                href={`/games/${today.game_id}/pregame`}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700"
              >
                프리게임 평가
              </Link>
              <Link
                href={`/games/${today.game_id}/postgame`}
                className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-4 py-2 text-sm font-semibold text-zinc-700 transition-colors hover:border-zinc-300 hover:bg-zinc-50"
              >
                포스트게임 리뷰
              </Link>
            </div>
          </div>
        </section>
      ) : (
        <section className="space-y-3">
          <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-400">
            오늘의 경기
          </h2>
          <div className="rounded-2xl border border-dashed border-zinc-300 bg-surface px-6 py-10 text-center text-sm text-zinc-400">
            오늘 예정된 경기가 없습니다.
          </div>
        </section>
      )}

      {/* Pipeline status grid — shown only when today's game exists */}
      {today && (
        <section className="space-y-4">
          <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-400">
            파이프라인 상태
          </h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {Object.entries(today.pipeline_status).map(([step, status]) => (
              <ScoreCard
                key={step}
                label={HOME_PIPELINE_LABEL_KO[step] ?? step}
                value={pipelineStatusKo(status)}
                tone={pipelineTone(status)}
              />
            ))}
          </div>
        </section>
      )}

      {/* Recent games table */}
      <section className="space-y-4">
        <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-400">
          최근 경기
        </h2>
        <DataTable<RecentGameSummary>
          columns={RECENT_COLUMNS}
          rows={recent}
          emptyMessage="최근 경기 기록이 없습니다."
          keyFn={(row) => row.game_id}
        />
      </section>
    </div>
  );
}
