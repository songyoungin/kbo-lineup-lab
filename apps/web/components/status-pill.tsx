import { cn } from "@/lib/utils";

export type StatusTone = "neutral" | "good" | "warning" | "danger" | "brand";

const TONE_CLASSES: Record<StatusTone, string> = {
  neutral: "bg-zinc-100 text-zinc-600 ring-zinc-200",
  good: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  warning: "bg-amber-50 text-amber-700 ring-amber-200",
  danger: "bg-rose-50 text-rose-700 ring-rose-200",
  brand: "bg-brand-50 text-brand-700 ring-brand-200",
};

export function StatusPill({
  tone = "neutral",
  children,
}: {
  tone?: StatusTone;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
        TONE_CLASSES[tone]
      )}
    >
      {children}
    </span>
  );
}
