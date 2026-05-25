import { DataTable, type Column } from "@/components/data-table";
import { StatusPill } from "@/components/status-pill";
import { ADMIN_STATUS_KO, ADMIN_STATUS_TONE, CATEGORY_KO } from "@/lib/i18n";
import type { CategoryStatusResponse } from "@/lib/types";

const COLUMNS: Column<CategoryStatusResponse>[] = [
  {
    header: "카테고리",
    accessor: (row) => CATEGORY_KO[row.category] ?? row.category,
  },
  {
    header: "상태",
    accessor: (row) => (
      <StatusPill tone={ADMIN_STATUS_TONE[row.status]}>
        {ADMIN_STATUS_KO[row.status]}
      </StatusPill>
    ),
  },
  {
    header: "페이로드 ID",
    align: "right",
    accessor: (row) =>
      row.raw_payload_id != null ? (
        <span className="font-mono text-xs">{row.raw_payload_id}</span>
      ) : (
        <span className="text-zinc-300">—</span>
      ),
  },
  {
    header: "스냅샷 ID",
    align: "right",
    accessor: (row) =>
      row.snapshot_id != null ? (
        <span className="font-mono text-xs">{row.snapshot_id}</span>
      ) : (
        <span className="text-zinc-300">—</span>
      ),
  },
  {
    header: "런 ID",
    align: "right",
    accessor: (row) =>
      row.run_id != null ? (
        <span className="font-mono text-xs">{row.run_id}</span>
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

export function IngestionStatusTable({
  categories,
}: {
  categories: CategoryStatusResponse[];
}) {
  return (
    <DataTable<CategoryStatusResponse>
      columns={COLUMNS}
      rows={categories}
      emptyMessage="카테고리 데이터 없음"
      keyFn={(row) => row.category}
    />
  );
}
