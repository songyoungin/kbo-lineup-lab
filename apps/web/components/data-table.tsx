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
    <div className="overflow-x-auto rounded-md border border-zinc-200">
      <table className="w-full text-sm text-left">
        <thead className="bg-zinc-50 border-b border-zinc-200">
          <tr>
            {columns.map((col) => (
              <th
                key={col.header}
                className={cn(
                  "px-3 py-2 text-xs font-semibold uppercase tracking-wide text-zinc-500",
                  ALIGN_CLASSES[col.align ?? "left"]
                )}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-100 bg-white">
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-3 py-6 text-center text-xs text-zinc-400"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr
                key={keyFn ? keyFn(row, i) : i}
                className="hover:bg-zinc-50 transition-colors"
              >
                {columns.map((col) => (
                  <td
                    key={col.header}
                    className={cn(
                      "px-3 py-2 text-zinc-700",
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
