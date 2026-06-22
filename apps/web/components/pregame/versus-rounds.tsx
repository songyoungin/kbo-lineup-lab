import { PlayerScoreCard } from "@/components/pregame/player-score-card";
import { DIFF_TYPE_KO } from "@/lib/i18n";
import type { LineupComparisonRow } from "@/lib/types";

// Header for one round (batting-order slot): order chip + difference-type label.
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
 * Per-order "manager pick vs AI pick" duel rounds.
 * Slots with different players show two cards facing each other; slots where the
 * same player appears but with a changed position/order show one card plus a
 * change note (avoids duplicating the same card twice).
 * If every slot matches, renders a draw notice.
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
                  playerId={row.actual_player_id}
                  playerName={row.actual_player_name}
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
