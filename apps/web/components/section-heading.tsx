import { cn } from "@/lib/utils";

/**
 * 신문 "kicker" 스타일 섹션 제목.
 * 크림슨 사각 마커 + 소형 대문자 라벨 + 우측으로 뻗는 헤어라인.
 */
export function SectionHeading({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-3 flex items-center gap-2.5", className)}>
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 bg-brand-600" />
      <h2 className="shrink-0 text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-500">
        {children}
      </h2>
      <span aria-hidden className="h-px flex-1 bg-rule" />
    </div>
  );
}
