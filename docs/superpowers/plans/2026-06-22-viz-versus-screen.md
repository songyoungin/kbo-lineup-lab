# AI vs 감독 Versus Screen (Phase 2 of Visual/Entertainment series) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the pregame evaluation as an entertaining "🤖 AI vs 🧢 감독" fighting-game-style versus screen — a theatrical hero banner for the headline expected-run duel, plus per-slot head-to-head "rounds" that reuse the Phase 1 player score cards.

**Architecture:** Purely a web/presentation change — no backend, no new endpoint, no new data. Every value is already fetched on the pregame page (`api.pregame` → `actual_score`/`recommended_score`/`score_gap`/`verdict`; `api.lineupComparison` → per-slot `rows`). Two new presentational components (`VersusHero`, `VersusRounds`) plus a small pure-helper module (`lib/versus.ts`) replace the existing `LineupScoreSummary` (score-duel) and the Phase 1 nine-card grid with the duel framing; `VersusRounds` reuses the existing `<PlayerScoreCard>` for each differing slot.

**Tech Stack:** Next.js 16 (webpack dev), React server components (no client hooks needed in the new components), Tailwind v4, the existing `StatusPill`/`SectionHeading`/`PlayerScoreCard` components. No new runtime dependency.

## Global Constraints

- **Web-only, additive-to-data:** no change under `apps/api/`; no new API call. The two new components consume data the pregame page already fetches. The deterministic engine and `output_hash` are untouched by definition (no backend in this phase).
- **Honest framing (binding):** the recommended lineup is the model's optimum, so `recommended_score ≥ actual_score` essentially always. Do NOT present a fabricated "win probability" or a fair two-sided fight. The gauge visualizes the **expected-run gap magnitude** (how far the manager's lineup sits from the AI optimum), and the result text is derived from the existing `verdict` (small gap → "감독 선전 / 막상막하"; large gap → "AI 판정승"). Real win probability is deferred to Phase 4.
- **Reuse Phase 1:** the per-slot rounds render the existing `<PlayerScoreCard gameId playerId playerName />` (from `apps/web/components/pregame/player-score-card.tsx`) — do not duplicate card markup.
- **No JS unit-test harness exists** in `apps/web` (verified: no vitest/jest/testing-library; scripts are dev/build/start/lint/format:check). The verification gate for every web task is `npm run build` (clean type-check + compile) + `pre-commit run --all-files` + a manual visual smoke. This is the established repo pattern (Phase 1 used it), not a shortcut. Keep all logic that *could* be unit-tested as small pure functions in `lib/versus.ts` so it is trivially inspectable.
- **English** for code, comments, commit messages; **Korean** only in user-facing UI strings (matches `score-duel.tsx`, which has inline Korean).
- **Next.js 16 caveat:** `npm run dev` runs `next dev --webpack` on purpose (see `apps/web/AGENTS.md`); open the app at `http://localhost:3000`, never `127.0.0.1:3000` (HMR cross-origin block breaks client hydration of `<PlayerScoreCard>`).
- **Commit convention:** commitizen, branch `feature/viz-versus-screen`. Never commit on `main`; never use the git `-C` path flag.
- **Pre-commit only** for lint/format (ruff/mypy/bandit/vulture/eslint/prettier/harness-drift) — never invoke formatters directly. Run `pre-commit run --all-files` before the final commit. Run `/harness-audit` before opening a PR (it gates `gh pr create`).

---

## File Structure

- Create `apps/web/lib/versus.ts` — pure helpers: the AI-edge gauge fraction and the verdict→duel-narrative map. One responsibility: the duel's display math/labels.
- Create `apps/web/components/pregame/versus-hero.tsx` — the hero banner (server component; consumes `PregameResponse`).
- Create `apps/web/components/pregame/versus-rounds.tsx` — the per-slot head-to-head rounds (consumes `gameId` + `LineupComparisonRow[]`; renders `<PlayerScoreCard>`).
- Modify `apps/web/app/games/[gameId]/pregame/page.tsx` — swap `LineupScoreSummary`→`VersusHero`, replace the Phase 1 nine-card section with a `VersusRounds` section, update imports.

The existing `apps/web/components/score-duel.tsx` and `apps/web/components/pregame/lineup-score-summary.tsx` are left in place (untouched) — they are no longer mounted on the pregame page but remain valid components; removing them is out of scope.

---

## Interfaces produced by this plan (consumed by Phases 3–4)

```
lib/versus.ts:
  VERSUS_FULL_SCALE: number                       // 0.15 RE → gauge full
  aiEdgeFraction(scoreGap: number): number        // clamp(|scoreGap|/FULL, 1), in [0,1]
  duelNarrative(verdict: Verdict): { resultKo: string; tone: StatusTone }

<VersusHero pregame={PregameResponse} />           // headline duel banner
<VersusRounds gameId={number} rows={LineupComparisonRow[]} />   // per-slot duels
```

Phase 4 (win-probability distribution) will likely augment `VersusHero`'s gauge with a real win probability — the gauge is isolated in `aiEdgeFraction` + the hero's wedge markup so that swap is local.

---

## Task 1: Versus helpers + hero banner

**Files:**
- Create: `apps/web/lib/versus.ts`
- Create: `apps/web/components/pregame/versus-hero.tsx`

**Interfaces:**
- Consumes (existing): `Verdict` type and `PregameResponse` from `apps/web/lib/types.ts` (fields `actual_score`, `recommended_score`, `score_gap`, `verdict` — all numbers/enum, verified present); `StatusTone` + `StatusPill` from `apps/web/components/status-pill.tsx`; `cn` from `apps/web/lib/utils.ts`.
- Produces: `VERSUS_FULL_SCALE`, `aiEdgeFraction`, `duelNarrative`, and `<VersusHero pregame={...} />`.

- [ ] **Step 1: Create the pure helper module**

Create `apps/web/lib/versus.ts`:

```typescript
import type { StatusTone } from "@/components/status-pill";
import type { Verdict } from "./types";

// 기대 득점 격차(추천 − 실제의 절댓값)를 게이지 가득참으로 매핑하는 스케일.
// score-duel.tsx의 GAP_FULL_SCALE와 같은 의도의 상수(둘 다 0.15 RE)지만,
// 그 컴포넌트의 로컬 상수와의 결합을 피하려고 여기서 독립적으로 정의한다.
export const VERSUS_FULL_SCALE = 0.15;

/**
 * 0–1로 정규화한 AI 우세 게이지 채움 비율(격차의 크기만, 방향은 무시).
 * score_gap = 실제 − 추천 이므로 절댓값을 쓴다.
 */
export function aiEdgeFraction(scoreGap: number): number {
  return Math.min(Math.abs(scoreGap) / VERSUS_FULL_SCALE, 1);
}

export interface DuelNarrative {
  resultKo: string;
  tone: StatusTone;
}

// 판정 서사: 추천 라인업은 최적화 산물이라 기대 득점이 항상 실제 이상이다.
// 따라서 '누가 이겼나'가 아니라 감독의 라인업이 AI 최적안에 얼마나 근접했는지를
// 기존 verdict로 전한다(격차가 작을수록 감독 선전).
const _DUEL_BY_VERDICT: Record<Verdict, DuelNarrative> = {
  "Nearly optimal": { resultKo: "막상막하 — 감독 선전", tone: "good" },
  Acceptable: { resultKo: "감독 선방", tone: "good" },
  Questionable: { resultKo: "AI 우세", tone: "warning" },
  "Low offensive efficiency": { resultKo: "AI 판정승", tone: "danger" },
};

export function duelNarrative(verdict: Verdict): DuelNarrative {
  return _DUEL_BY_VERDICT[verdict];
}
```

- [ ] **Step 2: Create the hero banner component**

Create `apps/web/components/pregame/versus-hero.tsx`:

```tsx
import { cn } from "@/lib/utils";
import { StatusPill } from "@/components/status-pill";
import { aiEdgeFraction, duelNarrative } from "@/lib/versus";
import type { PregameResponse } from "@/lib/types";

// 쐐기 최소 가시 폭 — 아주 작은 격차도 방향이 읽히도록.
const MIN_WEDGE_PCT = 1.5;

/**
 * "🤖 AI vs 🧢 감독" 대결 히어로 배너.
 * 양 코너에 추천/실제 라인업의 기대 득점을 마주 세우고, 중앙 게이지가 앞선 쪽으로
 * 뻗어 격차의 방향·크기를 보여준다. 추천은 최적화 산물이라 보통 AI가 앞서므로,
 * 판정 문구(duelNarrative)는 '감독이 얼마나 근접했나'를 verdict로 전한다.
 */
export function VersusHero({ pregame }: { pregame: PregameResponse }) {
  const aiLeads = pregame.recommended_score > pregame.actual_score;
  const tie = pregame.recommended_score === pregame.actual_score;
  const wedgePct = tie
    ? 0
    : Math.max(aiEdgeFraction(pregame.score_gap) * 50, MIN_WEDGE_PCT);
  const narrative = duelNarrative(pregame.verdict);
  const sign = pregame.score_gap >= 0 ? "+" : "";

  return (
    <div className="rounded-md border border-rule bg-surface px-6 py-6">
      {/* 페르소나 대결 스코어라인 */}
      <div className="flex items-stretch justify-between gap-4">
        <div className="flex-1">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-brand-600">
            🤖 AI · 추천 라인업
          </p>
          <p className="mt-1 text-4xl font-bold tabular-nums text-ink">
            {pregame.recommended_score.toFixed(3)}
          </p>
          <p className="mt-0.5 text-[11px] text-zinc-400">기대 득점</p>
        </div>
        <span
          aria-hidden
          className="self-center text-base font-black italic text-zinc-300"
        >
          VS
        </span>
        <div className="flex-1 text-right">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-500">
            🧢 감독 · 실제 라인업
          </p>
          <p className="mt-1 text-4xl font-bold tabular-nums text-ink">
            {pregame.actual_score.toFixed(3)}
          </p>
          <p className="mt-0.5 text-[11px] text-zinc-400">기대 득점</p>
        </div>
      </div>

      {/* 우세 게이지 — 중앙에서 앞선 쪽으로 쐐기가 뻗는다 */}
      <div className="relative mt-5 h-2 rounded-full bg-rule/50">
        <span
          aria-hidden
          className="absolute left-1/2 top-1/2 h-3.5 w-px -translate-x-1/2 -translate-y-1/2 bg-zinc-400"
        />
        <span
          className={cn(
            "absolute top-0 h-full rounded-full",
            aiLeads ? "bg-brand-600" : "bg-zinc-400"
          )}
          style={
            aiLeads
              ? { right: "50%", width: `${wedgePct}%` }
              : { left: "50%", width: `${wedgePct}%` }
          }
          aria-label={`AI 우세 게이지 ${wedgePct.toFixed(0)}%`}
        />
      </div>

      {/* 판정 */}
      <div className="mt-4 flex items-center justify-center gap-2.5">
        <span className="text-sm font-semibold tabular-nums text-zinc-600">
          Δ {sign}
          {pregame.score_gap.toFixed(3)}
        </span>
        <span className="text-[11px] text-zinc-400">실제 − 추천</span>
        <StatusPill tone={narrative.tone}>{narrative.resultKo}</StatusPill>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify the build compiles**

Run: `cd apps/web && npm run build`
Expected: `✓ Compiled successfully`, TypeScript clean, all routes generated. (The new component is exported but not yet mounted; an unused export does not fail the Next build — same as Phase 1 Task 2.)

- [ ] **Step 4: Commit**

```bash
git checkout -b feature/viz-versus-screen
git add apps/web/lib/versus.ts apps/web/components/pregame/versus-hero.tsx
git commit -m "feat(web): add AI-vs-manager versus hero banner and duel helpers"
```

---

## Task 2: Per-slot versus rounds

**Files:**
- Create: `apps/web/components/pregame/versus-rounds.tsx`

**Interfaces:**
- Consumes: `LineupComparisonRow` from `apps/web/lib/types.ts` (fields verified: `batting_order`, `actual_player_id`, `actual_player_name`, `actual_position`, `recommended_player_id`, `recommended_player_name`, `recommended_position`, `difference_type`, `main_reason`); `DIFF_TYPE_KO` from `apps/web/lib/i18n.ts`; `<PlayerScoreCard>` from `apps/web/components/pregame/player-score-card.tsx`.
- Produces: `<VersusRounds gameId={number} rows={LineupComparisonRow[]} />`.

- [ ] **Step 1: Create the rounds component**

Create `apps/web/components/pregame/versus-rounds.tsx`:

```tsx
import { PlayerScoreCard } from "@/components/pregame/player-score-card";
import { DIFF_TYPE_KO } from "@/lib/i18n";
import type { LineupComparisonRow } from "@/lib/types";

// 한 라운드(타순) 머리말: 타순 칩 + 차이 유형 라벨.
function RoundHeader({ row }: { row: LineupComparisonRow }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      <span className="rounded bg-zinc-900 px-1.5 py-0.5 text-[10px] font-bold text-white">
        {row.batting_order}번 타순
      </span>
      <span className="text-[11px] font-semibold text-zinc-500">
        {DIFF_TYPE_KO[row.difference_type]}
      </span>
    </div>
  );
}

/**
 * 타순별 "감독 픽 vs AI 픽" 대결 라운드.
 * 선수가 서로 다른 슬롯은 두 장의 카드를 마주 세우고, 같은 선수가 포지션/타순만
 * 바뀐 슬롯은 카드 한 장 + 변경 설명을 보여준다(같은 카드 두 장 중복 방지).
 * 모든 타순이 일치하면 무승부 안내를 렌더한다.
 */
export function VersusRounds({
  gameId,
  rows,
}: {
  gameId: number;
  rows: LineupComparisonRow[];
}) {
  const battles = rows.filter((r) => r.difference_type !== "Same");

  if (battles.length === 0) {
    return (
      <div className="rounded-md border border-rule bg-surface p-4 text-xs text-zinc-400">
        AI와 감독의 선택이 모든 타순에서 일치했습니다 — 무승부.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {battles.map((row) => {
        const samePlayer = row.actual_player_id === row.recommended_player_id;
        return (
          <div
            key={row.batting_order}
            className="rounded-md border border-rule bg-surface p-4"
          >
            <RoundHeader row={row} />

            {samePlayer ? (
              <div className="flex flex-col items-center gap-2">
                <PlayerScoreCard
                  gameId={gameId}
                  playerId={row.recommended_player_id}
                  playerName={row.recommended_player_name}
                />
                <p className="text-center text-xs text-zinc-500">
                  같은 선수 · 감독 {row.actual_position} → AI{" "}
                  {row.recommended_position}
                </p>
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-center gap-3">
                <div className="flex flex-col items-center gap-1">
                  <span className="text-[11px] font-bold uppercase tracking-wide text-zinc-500">
                    🧢 감독
                  </span>
                  <PlayerScoreCard
                    gameId={gameId}
                    playerId={row.actual_player_id}
                    playerName={row.actual_player_name}
                  />
                </div>
                <span
                  aria-hidden
                  className="text-sm font-black italic text-zinc-300"
                >
                  VS
                </span>
                <div className="flex flex-col items-center gap-1">
                  <span className="text-[11px] font-bold uppercase tracking-wide text-brand-600">
                    🤖 AI 제안
                  </span>
                  <PlayerScoreCard
                    gameId={gameId}
                    playerId={row.recommended_player_id}
                    playerName={row.recommended_player_name}
                  />
                </div>
              </div>
            )}

            <p className="mt-3 text-center text-xs text-zinc-500">
              {row.main_reason}
            </p>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Verify the build compiles**

Run: `cd apps/web && npm run build`
Expected: `✓ Compiled successfully`, TypeScript clean. (Still unmounted — builds as an unused export.)

- [ ] **Step 3: Commit**

```bash
git add apps/web/components/pregame/versus-rounds.tsx
git commit -m "feat(web): add per-slot AI-vs-manager versus rounds reusing score cards"
```

---

## Task 3: Wire the versus screen into the pregame page

**Files:**
- Modify: `apps/web/app/games/[gameId]/pregame/page.tsx`

**Interfaces:**
- Consumes: `<VersusHero>` (Task 1), `<VersusRounds>` (Task 2), the already-fetched `pregame` (`PregameResponse`) and `comparison` (`LineupComparisonResponse`, has `.rows`) on the page, and `gameId` (already `Number(rawId)` in scope).

- [ ] **Step 1: Update the imports**

In `apps/web/app/games/[gameId]/pregame/page.tsx`, replace the two import lines

```tsx
import { LineupScoreSummary } from "@/components/pregame/lineup-score-summary";
```
and
```tsx
import { PlayerScoreCard } from "@/components/pregame/player-score-card";
```

with

```tsx
import { VersusHero } from "@/components/pregame/versus-hero";
import { VersusRounds } from "@/components/pregame/versus-rounds";
```

(Leave the `LineupComparisonTable`, `PlayerComparisonPanel`, `SectionHeading` imports as they are.)

- [ ] **Step 2: Swap the score-summary section for the versus hero**

Replace the existing block

```tsx
      {/* 점수 요약 */}
      <section className="reveal reveal-1">
        <SectionHeading>점수 요약</SectionHeading>
        <LineupScoreSummary pregame={pregame} />
      </section>
```

with

```tsx
      {/* AI vs 감독 대결 */}
      <section className="reveal reveal-1">
        <SectionHeading>AI vs 감독 대결</SectionHeading>
        <VersusHero pregame={pregame} />
      </section>
```

- [ ] **Step 3: Replace the Phase 1 nine-card section with the versus rounds**

Replace the existing block

```tsx
      {/* 추천 라인업 선수 카드 */}
      <section className="space-y-3">
        <SectionHeading>추천 라인업 선수 카드</SectionHeading>
        <div className="flex flex-wrap gap-3">
          {pregame.recommended_lineup.map((row) => (
            <PlayerScoreCard
              key={row.player_id}
              gameId={gameId}
              playerId={row.player_id}
              playerName={row.player_name}
            />
          ))}
        </div>
      </section>
```

with

```tsx
      {/* 대결 라운드 */}
      <section className="reveal reveal-3 space-y-3">
        <SectionHeading>대결 라운드</SectionHeading>
        <VersusRounds gameId={gameId} rows={comparison.rows} />
      </section>
```

> Rationale: the nine-card grid (Phase 1) and the rounds both render `<PlayerScoreCard>`, but the rounds are the superior framing — they place the manager's pick beside the AI's pick with the reason, reusing the exact Phase 1 card. Keeping both would be redundant and card-heavy. The `recommended_lineup` map is no longer used on the page after this swap, which is why the `PlayerScoreCard` import moves into `VersusRounds`.

- [ ] **Step 4: Verify the build compiles**

Run: `cd apps/web && npm run build`
Expected: `✓ Compiled successfully`, TypeScript clean, no "unused variable" error (the `PlayerScoreCard` import and the `recommended_lineup` map are both gone from this file).

- [ ] **Step 5: Manual visual smoke against Supabase dev**

Follow the running-supabase-dev skill (API on :8000; web on **http://localhost:3000**, never 127.0.0.1). Open `http://localhost:3000/games/16/pregame`:
- The top "AI vs 감독 대결" section shows the hero: 🤖 AI and 🧢 감독 corners with expected-run totals, a center gauge tilted toward the leader, Δ, and a verdict-derived result pill (e.g. "막상막하 — 감독 선전" / "AI 판정승").
- The "대결 라운드" section shows one card-duel per differing slot: 🧢 감독 card VS 🤖 AI 제안 card (or a single card + change note when it's the same player), each with the `main_reason` caption.
- Cards populate (OVR + radar + badge) — confirms `<PlayerScoreCard>` still hydrates inside the rounds.
- No console errors.

- [ ] **Step 6: Run pre-commit across the repo**

Run: `pre-commit run --all-files`
Expected: ruff, mypy, bandit, vulture, eslint, prettier, harness drift all pass. Fix any findings, re-run.

- [ ] **Step 7: Commit**

```bash
git add "apps/web/app/games/[gameId]/pregame/page.tsx"
git commit -m "feat(web): reframe pregame page as the AI-vs-manager versus screen"
```

---

## Self-Review

**1. Spec coverage:**
- Fighting-game versus framing of the existing `score_gap` → `VersusHero` (Task 1). ✅
- Reuse of the Phase 1 card → `VersusRounds` renders `<PlayerScoreCard>` (Task 2). ✅
- Mounted on the pregame page → Task 3 swaps it in. ✅
- Honest (no fabricated win probability) → gauge is `aiEdgeFraction` over the expected-run gap; result text is verdict-derived; Global Constraints + `duelNarrative` doc make this explicit. ✅
- Win-probability gauge upgrade seam for Phase 4 → noted in Interfaces (isolated in `aiEdgeFraction` + hero wedge). ✅

**2. Placeholder scan:** No TBD/TODO. Every code step shows the complete file/edit. The web verification gate (build + pre-commit + manual smoke) is concrete and matches the documented absence of a JS test harness — not a placeholder for "write tests."

**3. Type consistency:**
- `aiEdgeFraction(scoreGap: number): number`, `duelNarrative(verdict: Verdict): DuelNarrative`, `VERSUS_FULL_SCALE` — identical names/signatures between Task 1's `lib/versus.ts` and the hero's usage. ✅
- `VersusHero` prop is `{ pregame: PregameResponse }`; the page passes `pregame={pregame}` (Task 3). ✅
- `VersusRounds` props `{ gameId: number; rows: LineupComparisonRow[] }`; the page passes `gameId={gameId} rows={comparison.rows}` (Task 3). ✅
- `LineupComparisonRow` field names used in `VersusRounds` (`actual_player_id`, `recommended_player_name`, `actual_position`, `recommended_position`, `difference_type`, `main_reason`, `batting_order`) all match `lib/types.ts` (verified). ✅
- `DIFF_TYPE_KO` is keyed by `DifferenceType` and `row.difference_type` is `DifferenceType` — but `VersusRounds` only indexes it for non-`"Same"` rows; `"Same"` is a valid key anyway, so no missing-key risk. ✅
- `duelNarrative` covers all four `Verdict` values via a `Record<Verdict, …>` (exhaustive). ✅

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-22-viz-versus-screen.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
