import { cn } from "@/lib/utils";
import type { StatusTone } from "./status-pill";

const ACCENT_CLASSES: Record<StatusTone, string> = {
  neutral: "border-l-zinc-300",
  good: "border-l-emerald-400",
  warning: "border-l-amber-400",
  danger: "border-l-red-400",
};

export function ScoreCard({
  label,
  value,
  helper,
  tone = "neutral",
}: {
  label: string;
  value: string | number;
  helper?: string;
  tone?: StatusTone;
}) {
  return (
    <div
      className={cn(
        "rounded-md border border-zinc-200 bg-white p-4 border-l-4",
        ACCENT_CLASSES[tone]
      )}
    >
      <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold text-zinc-900 tabular-nums">
        {value}
      </p>
      {helper && <p className="mt-1 text-xs text-zinc-400">{helper}</p>}
    </div>
  );
}
