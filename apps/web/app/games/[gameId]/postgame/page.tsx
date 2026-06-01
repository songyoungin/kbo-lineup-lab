import { notFound } from "next/navigation";
import Link from "next/link";
import { ApiError, api } from "@/lib/api";
import type { PostgameResponse } from "@/lib/types";
import { ResultSummary } from "@/components/postgame/result-summary";
import { PlayerOutcomeList } from "@/components/postgame/player-outcome-list";
import { ChoiceReviewCard } from "@/components/postgame/choice-review-card";

/** 모델 한계 섹션 */
function ModelLimitations({ limitations }: { limitations: string[] }) {
  if (limitations.length === 0) return null;
  return (
    <section className="space-y-2 rounded-xl border border-zinc-200/80 bg-zinc-50/70 p-4">
      <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-400">
        모델 한계
      </h2>
      <ul className="list-disc list-inside space-y-1">
        {limitations.map((item, i) => (
          <li key={i} className="text-xs text-zinc-500">
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 자연어 요약 섹션 */
function SummaryText({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section className="rounded-xl border border-zinc-200/80 bg-surface p-5 shadow-sm">
      <h2 className="mb-2 text-xs font-bold uppercase tracking-wider text-zinc-400">
        종합 평가
      </h2>
      <p className="text-sm leading-relaxed text-zinc-700">{text}</p>
    </section>
  );
}

export default async function PostgamePage({
  params,
}: {
  params: Promise<{ gameId: string }>;
}) {
  const { gameId: rawId } = await params;
  const gameId = Number(rawId);
  if (!Number.isFinite(gameId)) notFound();

  let review: PostgameResponse;
  try {
    review = await api.postgame(gameId);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }

  return (
    <div className="max-w-4xl space-y-8">
      {/* 헤더 */}
      <header className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-ink">
            포스트게임 리뷰
          </h1>
          <p className="mt-1 text-sm text-zinc-500">Game #{gameId}</p>
        </div>
        <Link
          href={`/games/${gameId}/pregame`}
          className="inline-flex items-center gap-1 text-sm font-semibold text-brand-700 transition-colors hover:text-brand-800"
        >
          ← 프리게임 평가 보기
        </Link>
      </header>

      {/* 결과 요약 */}
      <section>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-zinc-400">
          점수 요약
        </h2>
        <ResultSummary review={review} />
      </section>

      {/* 기대 이상 / 이하 선수 목록 */}
      <section>
        <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-zinc-400">
          선수 성과
        </h2>
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          <PlayerOutcomeList
            title="기대 이상"
            tone="good"
            players={review.overperformers}
          />
          <PlayerOutcomeList
            title="기대 이하"
            tone="danger"
            players={review.underperformers}
          />
          <PlayerOutcomeList
            title="기대치 부합"
            tone="neutral"
            players={review.other_actual}
          />
        </div>
      </section>

      {/* 선택 리뷰 카드 */}
      {review.difference_reviews.length > 0 && (
        <section>
          <h2 className="mb-3 text-xs font-bold uppercase tracking-wider text-zinc-400">
            선택 리뷰
          </h2>
          <div className="grid gap-3 md:grid-cols-2">
            {review.difference_reviews.map((d) => (
              <ChoiceReviewCard key={d.batting_order} review={d} />
            ))}
          </div>
        </section>
      )}

      {/* 종합 평가 (자연어 요약) */}
      <SummaryText text={review.summary_text} />

      {/* 모델 한계 */}
      <ModelLimitations limitations={review.model_limitations} />
    </div>
  );
}
