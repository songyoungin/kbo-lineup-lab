import { cn } from "@/lib/utils";
import { DIFF_TYPE_KO, DIFF_TONE } from "@/lib/i18n";
import type { LineupDifference, DifferenceType } from "@/lib/types";

const BORDER_CLASSES: Record<string, string> = {
  neutral: "border-zinc-200",
  good: "border-emerald-200",
  warning: "border-amber-300",
  danger: "border-red-300",
};

export function DifferenceCard({
  difference,
  actualName,
  recommendedName,
}: {
  difference: LineupDifference;
  actualName?: string;
  recommendedName?: string;
}) {
  const tone =
    DIFF_TONE[difference.difference_type as DifferenceType] ?? "neutral";
  const borderClass = BORDER_CLASSES[tone];

  return (
    <div
      className={cn("rounded-md border bg-white p-4 space-y-2", borderClass)}
    >
      {/* 상단: 타순 배지 + 차이 유형 */}
      <div className="flex items-center gap-2">
        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-zinc-100 text-xs font-bold text-zinc-600">
          {difference.batting_order}
        </span>
        <span className="text-xs font-medium text-zinc-500">
          {DIFF_TYPE_KO[difference.difference_type as DifferenceType] ??
            difference.difference_type}
        </span>
      </div>

      {/* 선수 비교 (이름이 있는 경우) */}
      {(actualName ?? recommendedName) && (
        <div className="flex items-center gap-2 text-sm">
          <span className="font-medium text-zinc-800">{actualName ?? "-"}</span>
          <span className="text-zinc-400 text-xs">→</span>
          <span className="font-medium text-zinc-800">
            {recommendedName ?? "-"}
          </span>
        </div>
      )}

      {/* 사유 */}
      <p className="text-xs text-zinc-600 leading-relaxed">
        {difference.main_reason}
      </p>
    </div>
  );
}
