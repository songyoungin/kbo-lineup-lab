import { ApiError, api } from "@/lib/api";
import type { IngestionRunSummary } from "@/lib/types";
import { DataTable, type Column } from "@/components/data-table";
import { StatusPill } from "@/components/status-pill";
import { IngestionStatusTable } from "@/components/admin/ingestion-status-table";

// MVP: 픽스처 게임 ID(1)로 직접 요청 — 실제 게임 선택 UI는 추후 추가
const FIXTURE_GAME_ID = 1;

// 런 상태 → StatusPill tone
function runStatusTone(status: string) {
  if (status === "completed") return "good" as const;
  if (status === "failed") return "danger" as const;
  if (status === "running") return "warning" as const;
  return "neutral" as const;
}

const RUN_STATUS_KO: Record<string, string> = {
  pending: "대기 중",
  running: "실행 중",
  completed: "완료",
  failed: "실패",
};

const RUN_COLUMNS: Column<IngestionRunSummary>[] = [
  {
    header: "ID",
    align: "right",
    accessor: (row) => <span className="font-mono text-xs">{row.id}</span>,
  },
  {
    header: "소스",
    accessor: (row) => (
      <span className="font-mono text-xs text-zinc-600 break-all">
        {row.source}
      </span>
    ),
  },
  {
    header: "상태",
    accessor: (row) => (
      <StatusPill tone={runStatusTone(row.status)}>
        {RUN_STATUS_KO[row.status] ?? row.status}
      </StatusPill>
    ),
  },
  {
    header: "시작",
    accessor: (row) =>
      row.started_at ? (
        <span className="text-xs text-zinc-500">
          {new Date(row.started_at).toLocaleString("ko-KR")}
        </span>
      ) : (
        <span className="text-zinc-300">—</span>
      ),
  },
  {
    header: "완료",
    accessor: (row) =>
      row.finished_at ? (
        <span className="text-xs text-zinc-500">
          {new Date(row.finished_at).toLocaleString("ko-KR")}
        </span>
      ) : (
        <span className="text-zinc-300">—</span>
      ),
  },
  {
    header: "에러",
    accessor: (row) =>
      row.error_message ? (
        <span
          className="text-xs text-red-600 max-w-xs truncate block"
          title={row.error_message}
        >
          {row.error_message}
        </span>
      ) : (
        <span className="text-zinc-300">—</span>
      ),
  },
];

export default async function AdminIngestionPage() {
  const [runsResult, gameStatusResult] = await Promise.allSettled([
    api.adminIngestionRuns(),
    api.adminGameIngestionStatus(FIXTURE_GAME_ID),
  ]);

  const runs = runsResult.status === "fulfilled" ? runsResult.value.runs : [];
  const runsError =
    runsResult.status === "rejected"
      ? (runsResult.reason as Error).message
      : null;

  const gameStatus =
    gameStatusResult.status === "fulfilled" ? gameStatusResult.value : null;
  const gameStatusError =
    gameStatusResult.status === "rejected"
      ? (gameStatusResult.reason as Error).message
      : null;
  // 404는 게임 데이터 미수집으로 표시 (에러로 올리지 않음)
  const gameNotFound =
    gameStatusResult.status === "rejected" &&
    gameStatusResult.reason instanceof ApiError &&
    gameStatusResult.reason.status === 404;

  return (
    <div className="space-y-8 max-w-5xl">
      {/* 헤더 */}
      <header>
        <h1 className="text-xl font-bold text-zinc-900">
          파이프라인 수집 현황
        </h1>
        <p className="text-sm text-zinc-500 mt-0.5">
          KBO 데이터 수집·정규화·분석 파이프라인 상태를 실시간으로 확인합니다.
        </p>
      </header>

      {/* 최근 수집 런 섹션 */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-zinc-700 uppercase tracking-wide">
          최근 수집 런
        </h2>
        {runsError ? (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
            수집 런 목록을 불러오지 못했습니다: {runsError}
          </div>
        ) : (
          <DataTable<IngestionRunSummary>
            columns={RUN_COLUMNS}
            rows={runs}
            emptyMessage="수집 런 기록이 없습니다."
            keyFn={(row) => row.id}
          />
        )}
      </section>

      {/* 게임별 수집 상태 섹션 */}
      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold text-zinc-700 uppercase tracking-wide">
            게임별 수집 상태
          </h2>
          {gameStatus && (
            <p className="text-xs text-zinc-400 mt-0.5">
              Game #{gameStatus.game_id} · 외부 ID:{" "}
              {gameStatus.game_external_id} · {gameStatus.game_date}
            </p>
          )}
        </div>

        {gameNotFound ? (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700">
            게임 데이터가 아직 수집되지 않았습니다 (Game #{FIXTURE_GAME_ID}).
          </div>
        ) : gameStatusError ? (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
            게임 수집 상태를 불러오지 못했습니다: {gameStatusError}
          </div>
        ) : gameStatus ? (
          <IngestionStatusTable categories={gameStatus.categories} />
        ) : null}
      </section>
    </div>
  );
}
