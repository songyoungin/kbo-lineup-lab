"use client";

import { useEffect, useReducer } from "react";
import { api } from "@/lib/api";
import type { PlayerScoreCardResponse } from "@/lib/types";

type FetchState =
  | { status: "loading" }
  | { status: "success"; data: PlayerScoreCardResponse }
  | { status: "error"; message: string };

type FetchAction =
  | { type: "loading" }
  | { type: "success"; data: PlayerScoreCardResponse }
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

const BADGE_STYLE: Record<string, string> = {
  HOT: "bg-rose-100 text-rose-600",
  COLD: "bg-sky-100 text-sky-600",
  NEUTRAL: "bg-zinc-100 text-zinc-500",
};

const BADGE_LABEL: Record<string, string> = {
  HOT: "🔥 HOT",
  COLD: "🧊 COLD",
  NEUTRAL: "— EVEN",
};

// Pentagon radar over the 5 axes (0–100). Pure SVG, no chart dependency.
function Radar({ values }: { values: number[] }) {
  const size = 132;
  const c = size / 2;
  const r = c - 18;
  const n = values.length; // 5
  const point = (i: number, mag: number) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
    const rad = (mag / 100) * r;
    return [c + rad * Math.cos(angle), c + rad * Math.sin(angle)];
  };
  const grid = [25, 50, 75, 100].map((g) =>
    values.map((_, i) => point(i, g).join(",")).join(" ")
  );
  const poly = values.map((v, i) => point(i, v).join(",")).join(" ");
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="h-32 w-32">
      {grid.map((g, i) => (
        <polygon
          key={i}
          points={g}
          className="fill-none stroke-zinc-200"
          strokeWidth={1}
        />
      ))}
      {values.map((_, i) => {
        const [x, y] = point(i, 100);
        return (
          <line
            key={i}
            x1={c}
            y1={c}
            x2={x}
            y2={y}
            className="stroke-zinc-200"
            strokeWidth={1}
          />
        );
      })}
      <polygon
        points={poly}
        className="fill-brand-400/30 stroke-brand-500"
        strokeWidth={2}
      />
    </svg>
  );
}

export function PlayerScoreCard({
  gameId,
  playerId,
  playerName,
}: {
  gameId: number;
  playerId: number;
  playerName: string;
}) {
  const [state, dispatch] = useReducer(reducer, { status: "loading" });

  useEffect(() => {
    let cancelled = false;
    async function run() {
      dispatch({ type: "loading" });
      try {
        const data = await api.playerScoreCard(gameId, playerId);
        if (!cancelled) dispatch({ type: "success", data });
      } catch (e) {
        if (!cancelled)
          dispatch({ type: "error", message: (e as Error).message });
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [gameId, playerId]);

  if (state.status === "loading") {
    return (
      <div className="h-64 w-44 animate-pulse rounded-xl border border-rule bg-surface" />
    );
  }
  if (state.status === "error") {
    return (
      <div className="flex h-64 w-44 items-center justify-center rounded-xl border border-rule bg-surface p-2 text-center text-xs text-rose-500">
        {playerName}
        <br />
        불러오기 실패
      </div>
    );
  }

  const d = state.data;
  return (
    <div className="flex w-44 flex-col items-center gap-1 rounded-xl border border-rule bg-gradient-to-b from-white to-brand-50 p-3 shadow-sm">
      <div className="flex w-full items-start justify-between">
        <span className="text-3xl font-black tabular-nums text-brand-600">
          {d.overall}
        </span>
        <span className="rounded bg-zinc-900 px-1.5 py-0.5 text-[10px] font-bold text-white">
          {d.position}
        </span>
      </div>
      <div className="w-full truncate text-sm font-bold text-zinc-900">
        {d.player_name}
      </div>
      <Radar values={d.factors.map((f) => f.axis_score)} />
      <span
        className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${BADGE_STYLE[d.form_badge]}`}
      >
        {BADGE_LABEL[d.form_badge]}
      </span>
      <div className="mt-1 w-full space-y-0.5 text-[10px] text-zinc-500">
        {d.factors.map((f) => (
          <div key={f.component} className="flex justify-between">
            <span>{f.label_ko}</span>
            <span className="tabular-nums text-zinc-700">
              {Math.round(f.axis_score)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
