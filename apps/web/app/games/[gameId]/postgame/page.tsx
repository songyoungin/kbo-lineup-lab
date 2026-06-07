import { notFound } from "next/navigation";
import Link from "next/link";
import { ApiError, api } from "@/lib/api";
import type { PostgameResponse } from "@/lib/types";
import { ResultSummary } from "@/components/postgame/result-summary";
import { PlayerOutcomeList } from "@/components/postgame/player-outcome-list";
import { ChoiceReviewGroups } from "@/components/postgame/choice-review-groups";
import { SectionHeading } from "@/components/section-heading";
import { modelLimitationKo, summaryTextKo } from "@/lib/i18n";

/** 모델 한계 섹션 */
function ModelLimitations({ limitations }: { limitations: string[] }) {
  if (limitations.length === 0) return null;
  return (
    <section className="space-y-2 rounded-md border border-rule bg-canvas p-4">
      <h2 className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-400">
        모델 한계
      </h2>
      <ul className="list-inside list-disc space-y-1">
        {limitations.map((item, i) => (
          <li key={i} className="text-xs text-zinc-500">
            {modelLimitationKo(item)}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 헤드라인 내러티브 — 신문 1면 리드 기사 스타일 */
function NarrativeStory({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section>
      <div className="rule-double" />
      <h2 className="mt-4 text-2xl font-bold tracking-tight text-ink">
        오늘의 이야기
      </h2>
      <p className="mt-1.5 text-[11px] font-medium uppercase tracking-[0.18em] text-zinc-400">
        Lineup Lab 포스트게임 칼럼
      </p>
      <p className="drop-cap mt-4 whitespace-pre-line text-[16px] leading-[1.9] text-zinc-800 lg:columns-2 lg:gap-10 lg:text-[15px]">
        {text}
      </p>
      <div className="mt-6 h-px bg-rule" />
    </section>
  );
}

/** 자연어 요약 섹션 */
function SummaryText({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section className="rounded-md border border-rule bg-surface p-5">
      <h2 className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-400">
        종합 평가
      </h2>
      <p className="text-sm leading-relaxed text-zinc-700">
        {summaryTextKo(text)}
      </p>
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
      <header className="reveal flex items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink">
            포스트게임 리뷰
          </h1>
          <p className="mt-1.5 text-sm tabular-nums text-zinc-500">
            Game #{gameId}
          </p>
        </div>
        <Link
          href={`/games/${gameId}/pregame`}
          className="inline-flex items-center gap-1 text-sm font-semibold text-brand-700 transition-colors hover:text-brand-800"
        >
          ← 프리게임 평가 보기
        </Link>
      </header>

      {/* 결과 요약 */}
      <section className="reveal reveal-1">
        <SectionHeading>점수 요약</SectionHeading>
        <ResultSummary review={review} />
      </section>

      {/* 헤드라인 내러티브 — 리드 스토리 */}
      <div className="reveal reveal-2">
        <NarrativeStory text={review.narrative} />
      </div>

      {/* 기대 이상 / 이하 선수 목록 */}
      <section className="reveal reveal-3">
        <SectionHeading>선수 성과</SectionHeading>
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

      {/* 선택 리뷰 — 결과 기준 그룹핑 */}
      {review.difference_reviews.length > 0 && (
        <section className="reveal reveal-4">
          <SectionHeading>선택 리뷰</SectionHeading>
          <ChoiceReviewGroups reviews={review.difference_reviews} />
        </section>
      )}

      {/* 종합 평가 (자연어 요약) */}
      <div className="reveal reveal-5">
        <SummaryText text={review.summary_text} />
      </div>

      {/* 모델 한계 */}
      <div className="reveal reveal-5">
        <ModelLimitations limitations={review.model_limitations} />
      </div>
    </div>
  );
}
