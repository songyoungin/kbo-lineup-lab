import { cn } from "@/lib/utils";
import type { StatusTone } from "./status-pill";

const ACCENT_CLASSES: Record<StatusTone, string> = {
  neutral: "border-l-zinc-300",
  good: "border-l-emerald-400",
  warning: "border-l-amber-400",
  danger: "border-l-rose-400",
  brand: "border-l-brand-600",
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
        "rounded-xl border border-l-4 border-zinc-200/80 bg-surface p-4 shadow-sm",
        ACCENT_CLASSES[tone]
      )}
    >
      <p className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
        {label}
      </p>
      <p className="mt-1.5 text-2xl font-bold tabular-nums text-ink">{value}</p>
      {helper && <p className="mt-1 text-xs text-zinc-400">{helper}</p>}
    </div>
  );
}
