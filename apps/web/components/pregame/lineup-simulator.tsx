"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { LineupRow, LineupScoreResponse } from "@/lib/types";

type FetchState =
  | { status: "loading" }
  | { status: "success"; data: LineupScoreResponse }
  | { status: "error"; message: string };

type FetchAction =
  | { type: "loading" }
  | { type: "success"; data: LineupScoreResponse }
  | { type: "error"; message: string };

function reducer(_s: FetchState, a: FetchAction): FetchState {
  switch (a.type) {
    case "loading":
      return { status: "loading" };
    case "success":
      return { status: "success", data: a.data };
    case "error":
      return { status: "error", message: a.message };
  }
}

// Pure: move item at `from` to `to`, returning a new array.
function reorder<T>(list: T[], from: number, to: number): T[] {
  const next = list.slice();
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

// Delta colour: positive (better than recommended) = brand; negative = zinc.
function deltaTone(delta: number): string {
  if (delta > 0.0005) return "text-brand-600";
  if (delta < -0.0005) return "text-zinc-400";
  return "text-zinc-500";
}

export function LineupSimulator({
  gameId,
  recommendedLineup,
}: {
  gameId: number;
  recommendedLineup: LineupRow[];
}) {
  // Canonical recommended order (sorted by batting_order), used as the reset baseline.
  const baseline = [...recommendedLineup].sort(
    (a, b) => a.batting_order - b.batting_order
  );
  const [order, setOrder] = useState<LineupRow[]>(baseline);
  const [state, dispatch] = useReducer(reducer, { status: "loading" });
  const dragFrom = useRef<number | null>(null);
  // Monotonic token so a slow earlier response can't overwrite a newer one.
  const reqToken = useRef(0);

  useEffect(() => {
    const token = ++reqToken.current;
    dispatch({ type: "loading" });
    api
      .lineupScore(
        gameId,
        order.map((r) => r.player_id)
      )
      .then((data) => {
        if (token === reqToken.current) dispatch({ type: "success", data });
      })
      .catch((e) => {
        if (token === reqToken.current)
          dispatch({ type: "error", message: (e as Error).message });
      });
  }, [gameId, order]);

  const move = (from: number, to: number) => {
    if (to < 0 || to >= order.length || from === to) return;
    setOrder((cur) => reorder(cur, from, to));
  };

  const isModified = order.some(
    (r, i) => r.player_id !== baseline[i].player_id
  );

  const data = state.status === "success" ? state.data : null;

  return (
    <div className="rounded-md border border-rule bg-surface p-4">
      {/* Headline: live expected runs + delta vs recommended */}
      <div className="mb-4 flex items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-500">
            기대 득점
          </p>
          <p className="mt-1 text-4xl font-bold tabular-nums text-ink">
            {data ? data.total_score.toFixed(3) : "—"}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-zinc-500">
            추천 대비
          </p>
          <p
            className={`mt-1 text-2xl font-bold tabular-nums ${
              data ? deltaTone(data.delta_vs_recommended) : "text-zinc-400"
            }`}
          >
            {data
              ? `${data.delta_vs_recommended >= 0 ? "+" : ""}${data.delta_vs_recommended.toFixed(3)}`
              : "—"}
          </p>
        </div>
      </div>

      {state.status === "error" && (
        <p className="mb-3 text-xs text-rose-500">
          점수를 불러오지 못했습니다.
        </p>
      )}

      {/* Draggable batting order */}
      <ol className="space-y-1.5">
        {order.map((row, i) => (
          <li
            key={row.player_id}
            draggable
            onDragStart={() => {
              dragFrom.current = i;
            }}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (dragFrom.current !== null) move(dragFrom.current, i);
              dragFrom.current = null;
            }}
            className="flex cursor-grab items-center gap-3 rounded-md border border-rule bg-white px-3 py-2 active:cursor-grabbing"
          >
            <span className="w-5 text-center text-sm font-bold tabular-nums text-brand-600">
              {i + 1}
            </span>
            <span aria-hidden className="text-zinc-300">
              ⠿
            </span>
            <span className="flex-1 text-sm font-semibold text-zinc-900">
              {row.player_name}
            </span>
            <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-bold text-zinc-500">
              {row.position}
            </span>
            {/* Accessible / touch fallback for reordering */}
            <span className="flex flex-col">
              <button
                type="button"
                aria-label={`${i + 1}번 타자 위로`}
                disabled={i === 0}
                onClick={() => move(i, i - 1)}
                className="leading-none text-zinc-400 disabled:opacity-30"
              >
                ▲
              </button>
              <button
                type="button"
                aria-label={`${i + 1}번 타자 아래로`}
                disabled={i === order.length - 1}
                onClick={() => move(i, i + 1)}
                className="leading-none text-zinc-400 disabled:opacity-30"
              >
                ▼
              </button>
            </span>
          </li>
        ))}
      </ol>

      <div className="mt-3 flex items-center justify-between">
        <p className="text-[11px] text-zinc-400">
          드래그하거나 ▲▼로 타순을 바꿔 보세요.
        </p>
        <button
          type="button"
          disabled={!isModified}
          onClick={() => setOrder(baseline)}
          className="rounded-md border border-rule px-2.5 py-1 text-xs font-semibold text-zinc-600 disabled:opacity-40"
        >
          추천 타순으로 초기화
        </button>
      </div>
    </div>
  );
}
