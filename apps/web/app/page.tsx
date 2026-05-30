import Link from "next/link";
import { ApiError, api } from "@/lib/api";
import { MOCK_TEAM_HOME } from "@/lib/mock";
import type { TeamHomeResponse, Verdict } from "@/lib/types";
import { ScoreCard } from "@/components/score-card";
import { StatusPill } from "@/components/status-pill";
import { DataTable, type Column } from "@/components/data-table";
import type { StatusTone } from "@/components/status-pill";
import type { RecentGameSummary } from "@/lib/types";

// Map verdict literal → status tone for visual colouring
function verdictTone(verdict: Verdict | null): StatusTone {
  if (!verdict) return "neutral";
  const verdictMap: Record<Verdict, StatusTone> = {
    "Nearly optimal": "good",
    Acceptable: "good",
    Questionable: "warning",
    "Low offensive efficiency": "danger",
  };
  return verdictMap[verdict];
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
    accessor: (row) => row.opponent_team_code,
  },
  {
    header: "평가",
    accessor: (row) =>
      row.verdict ? (
        <StatusPill tone={verdictTone(row.verdict)}>{row.verdict}</StatusPill>
      ) : (
        <span className="text-zinc-400 text-xs">-</span>
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
    <div className="space-y-6 max-w-3xl">
      {/* Mock fallback notice */}
      {usingMock && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-700">
          API에 연결할 수 없어 픽스처 형태의 모의 데이터를 표시하고 있습니다.
        </div>
      )}

      {/* Page header */}
      <div>
        <h1 className="text-lg font-semibold text-zinc-900">LG 트윈스 팀 홈</h1>
        <p className="mt-0.5 text-xs text-zinc-400">
          팀 코드: {home.team_code}
        </p>
      </div>

      {/* Today's game card */}
      {today ? (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-zinc-700">오늘의 경기</h2>
          <div className="rounded-md border border-zinc-200 bg-white p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-base font-semibold text-zinc-900">
                  LG vs {today.opponent_team_code}
                </p>
                <p className="text-xs text-zinc-400 mt-0.5">
                  {today.game_date}
                  {today.venue && ` · ${today.venue}`}
                  {today.opponent_starter &&
                    ` · 상대 선발: ${today.opponent_starter}`}
                </p>
              </div>
              <div className="text-xs text-zinc-400">Game #{today.game_id}</div>
            </div>

            {/* Pipeline status pills */}
            <div className="flex flex-wrap gap-2">
              {Object.entries(today.pipeline_status).map(([step, status]) => (
                <div key={step} className="flex items-center gap-1">
                  <span className="text-xs text-zinc-500">{step}</span>
                  <StatusPill tone={pipelineTone(status)}>{status}</StatusPill>
                </div>
              ))}
            </div>

            {/* 분석 페이지 링크 */}
            <div className="flex gap-3 pt-1">
              <Link
                href={`/games/${today.game_id}/pregame`}
                className="text-xs text-zinc-500 hover:text-zinc-800 underline underline-offset-2 transition-colors"
              >
                프리게임 평가
              </Link>
              <Link
                href={`/games/${today.game_id}/postgame`}
                className="text-xs text-zinc-500 hover:text-zinc-800 underline underline-offset-2 transition-colors"
              >
                포스트게임 리뷰
              </Link>
            </div>
          </div>
        </section>
      ) : (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-zinc-700">오늘의 경기</h2>
          <p className="text-xs text-zinc-400">오늘 예정된 경기가 없습니다.</p>
        </section>
      )}

      {/* Summary score cards — shown only when today's game exists */}
      {today && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Object.entries(today.pipeline_status).map(([step, status]) => (
            <ScoreCard
              key={step}
              label={step}
              value={status}
              tone={pipelineTone(status)}
            />
          ))}
        </div>
      )}

      {/* Recent games table */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-zinc-700">최근 경기</h2>
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
