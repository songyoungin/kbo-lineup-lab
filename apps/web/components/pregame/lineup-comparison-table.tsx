import { DataTable, type Column } from "@/components/data-table";
import { StatusPill } from "@/components/status-pill";
import { DIFF_TYPE_KO, DIFF_TONE } from "@/lib/i18n";
import type { DifferenceType, LineupComparisonRow } from "@/lib/types";

const COLUMNS: Column<LineupComparisonRow>[] = [
  {
    header: "타순",
    // 라인업 카드처럼 타순 번호를 원형 배지로 — 차이가 있는 슬롯만 크림슨.
    accessor: (row) => (
      <span
        className={
          row.difference_type === "Same"
            ? "inline-flex h-6 w-6 items-center justify-center rounded-full bg-ink/5 text-xs font-bold text-zinc-500"
            : "inline-flex h-6 w-6 items-center justify-center rounded-full bg-brand-600 text-xs font-bold text-white"
        }
      >
        {row.batting_order}
      </span>
    ),
    align: "center",
  },
  {
    header: "실제",
    accessor: (row) => (
      <span className="text-xs">
        <span className="font-medium text-zinc-900">
          {row.actual_player_name}
        </span>{" "}
        <span className="text-zinc-400">{row.actual_position}</span>
      </span>
    ),
  },
  {
    header: "추천",
    accessor: (row) => (
      <span className="text-xs">
        <span className="font-medium text-zinc-900">
          {row.recommended_player_name}
        </span>{" "}
        <span className="text-zinc-400">{row.recommended_position}</span>
      </span>
    ),
  },
  {
    header: "차이",
    accessor: (row) => (
      <StatusPill tone={DIFF_TONE[row.difference_type as DifferenceType]}>
        {DIFF_TYPE_KO[row.difference_type as DifferenceType] ??
          row.difference_type}
      </StatusPill>
    ),
  },
  {
    header: "사유",
    accessor: (row) => (
      <span className="text-xs text-zinc-600 max-w-sm block whitespace-normal leading-relaxed">
        {row.main_reason}
      </span>
    ),
  },
];

export function LineupComparisonTable({
  rows,
}: {
  rows: LineupComparisonRow[];
}) {
  return (
    <div className="overflow-x-auto">
      <DataTable<LineupComparisonRow>
        columns={COLUMNS}
        rows={rows}
        emptyMessage="비교 데이터가 없습니다."
        keyFn={(row) => row.batting_order}
      />
    </div>
  );
}
