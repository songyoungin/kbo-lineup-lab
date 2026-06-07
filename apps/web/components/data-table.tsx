import { cn } from "@/lib/utils";

export interface Column<Row> {
  header: string;
  accessor: (row: Row) => React.ReactNode;
  align?: "left" | "right" | "center";
}

const ALIGN_CLASSES: Record<NonNullable<Column<unknown>["align"]>, string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

export function DataTable<Row>({
  columns,
  rows,
  emptyMessage = "데이터 없음",
  keyFn,
}: {
  columns: Column<Row>[];
  rows: Row[];
  emptyMessage?: string;
  keyFn?: (row: Row, index: number) => string | number;
}) {
  return (
    <div className="overflow-x-auto rounded-md border border-rule bg-surface">
      {/* Box-score styling: bold ink rule under the header row, paper hairlines between rows. */}
      <table className="w-full text-left text-sm">
        <thead className="border-b-2 border-ink/80">
          <tr>
            {columns.map((col) => (
              <th
                key={col.header}
                className={cn(
                  "px-4 py-2.5 text-[11px] font-bold uppercase tracking-wider text-zinc-500",
                  ALIGN_CLASSES[col.align ?? "left"]
                )}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-rule/60">
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-4 py-8 text-center text-xs text-zinc-400"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr
                key={keyFn ? keyFn(row, i) : i}
                className="transition-colors hover:bg-brand-50/40"
              >
                {columns.map((col) => (
                  <td
                    key={col.header}
                    className={cn(
                      "px-4 py-2.5 text-zinc-700",
                      ALIGN_CLASSES[col.align ?? "left"]
                    )}
                  >
                    {col.accessor(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
