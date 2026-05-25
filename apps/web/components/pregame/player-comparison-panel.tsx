"use client";

import { useEffect, useReducer, useState } from "react";
import { api } from "@/lib/api";
import type {
  LineupDifference,
  PlayerComparisonResponse,
  PlayerComparisonStats,
} from "@/lib/types";

// 비동기 fetch 상태 타입
type FetchState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; data: PlayerComparisonResponse }
  | { status: "error"; message: string };

type FetchAction =
  | { type: "loading" }
  | { type: "success"; data: PlayerComparisonResponse }
  | { type: "error"; message: string };

function fetchReducer(_state: FetchState, action: FetchAction): FetchState {
  switch (action.type) {
    case "loading":
      return { status: "loading" };
    case "success":
      return { status: "success", data: action.data };
    case "error":
      return { status: "error", message: action.message };
  }
}

// 수치 포맷 헬퍼
function fmt(v: number | null | undefined, digits = 3): string {
  if (v == null) return "-";
  return v.toFixed(digits);
}

// 두 수치 비교: higher가 better면 양수가 "good"
function diffClass(a: number | null, b: number | null): string {
  if (a == null || b == null) return "text-zinc-700";
  if (a > b) return "text-emerald-600 font-semibold";
  if (a < b) return "text-red-500";
  return "text-zinc-700";
}

interface StatRow {
  label: string;
  actual: number | null;
  recommended: number | null;
  digits?: number;
}

function buildStatRows(
  actual: PlayerComparisonStats,
  recommended: PlayerComparisonStats
): StatRow[] {
  return [
    {
      label: "최근 14일 OPS",
      actual: actual.recent_14d_ops,
      recommended: recommended.recent_14d_ops,
    },
    {
      label: "최근 30일 OPS",
      actual: actual.recent_30d_ops,
      recommended: recommended.recent_30d_ops,
    },
    { label: "시즌 OPS", actual: actual.ops, recommended: recommended.ops },
    { label: "시즌 OBP", actual: actual.obp, recommended: recommended.obp },
    { label: "시즌 SLG", actual: actual.slg, recommended: recommended.slg },
    {
      label: "우투 상대 OPS",
      actual: actual.vs_rhp_ops,
      recommended: recommended.vs_rhp_ops,
    },
    {
      label: "좌투 상대 OPS",
      actual: actual.vs_lhp_ops,
      recommended: recommended.vs_lhp_ops,
    },
    {
      label: "우투 상대 PA",
      actual: actual.pa_vs_rhp,
      recommended: recommended.pa_vs_rhp,
      digits: 0,
    },
    {
      label: "좌투 상대 PA",
      actual: actual.pa_vs_lhp,
      recommended: recommended.pa_vs_lhp,
      digits: 0,
    },
    {
      label: "최근 5경기 선발",
      actual: actual.starts_last_5,
      recommended: recommended.starts_last_5,
      digits: 0,
    },
    {
      label: "모델 점수",
      actual: actual.model_score,
      recommended: recommended.model_score,
    },
  ];
}

export function PlayerComparisonPanel({
  gameId,
  differences,
}: {
  gameId: number;
  differences: LineupDifference[];
}) {
  // 실제로 차이가 있는 슬롯만 선택 대상
  const slots = differences
    .filter((d) => d.difference_type !== "Same")
    .map((d) => d.batting_order);

  const [slot, setSlot] = useState<number>(slots[0] ?? 1);
  const [state, dispatch] = useReducer(fetchReducer, { status: "idle" });

  useEffect(() => {
    let cancelled = false;

    async function fetchData() {
      dispatch({ type: "loading" });
      try {
        const result = await api.playerCompare(gameId, slot);
        if (!cancelled) dispatch({ type: "success", data: result });
      } catch (e) {
        if (!cancelled)
          dispatch({ type: "error", message: (e as Error).message });
      }
    }

    void fetchData();

    return () => {
      cancelled = true;
    };
  }, [gameId, slot]);

  const loading = state.status === "loading";
  const error = state.status === "error" ? state.message : null;
  const data = state.status === "success" ? state.data : null;

  if (slots.length === 0) {
    return (
      <div className="rounded-md border border-zinc-200 bg-white p-4 text-xs text-zinc-400">
        비교할 차이 슬롯이 없습니다.
      </div>
    );
  }

  return (
    <div className="border border-zinc-200 rounded-md bg-white divide-y divide-zinc-100">
      {/* 슬롯 선택 드롭다운 */}
      <div className="px-4 py-3 flex items-center gap-3">
        <label
          htmlFor="slot-select"
          className="text-xs font-semibold text-zinc-500 uppercase tracking-wide"
        >
          타순 선택
        </label>
        <select
          id="slot-select"
          value={slot}
          onChange={(e) => setSlot(Number(e.target.value))}
          className="text-sm border border-zinc-200 rounded px-2 py-1 text-zinc-700 bg-white focus:outline-none focus:ring-1 focus:ring-zinc-300"
        >
          {slots.map((s) => (
            <option key={s} value={s}>
              {s}번 타자
            </option>
          ))}
        </select>
      </div>

      {/* 콘텐츠 영역 */}
      <div className="p-4">
        {loading && (
          <div className="text-xs text-zinc-400 py-6 text-center">
            불러오는 중...
          </div>
        )}

        {error && !loading && (
          <div className="text-xs text-red-500 py-4 text-center">{error}</div>
        )}

        {data && !loading && (
          <div className="space-y-4">
            {/* 선수 이름 헤더 */}
            <div className="grid grid-cols-3 gap-2 text-xs font-semibold">
              <div className="text-zinc-500 uppercase tracking-wide">항목</div>
              <div className="text-zinc-900 text-center">
                실제 ({data.actual.player_name})
                <span className="block font-normal text-zinc-400">
                  {data.actual.position}
                </span>
              </div>
              <div className="text-zinc-900 text-center">
                추천 ({data.recommended.player_name})
                <span className="block font-normal text-zinc-400">
                  {data.recommended.position}
                </span>
              </div>
            </div>

            {/* 스탯 비교 테이블 */}
            <div className="divide-y divide-zinc-50 text-xs">
              {buildStatRows(data.actual, data.recommended).map((row) => (
                <div key={row.label} className="grid grid-cols-3 gap-2 py-1.5">
                  <span className="text-zinc-500">{row.label}</span>
                  <span
                    className={`text-center tabular-nums ${diffClass(row.actual, row.recommended)}`}
                  >
                    {fmt(row.actual, row.digits)}
                  </span>
                  <span
                    className={`text-center tabular-nums ${diffClass(row.recommended, row.actual)}`}
                  >
                    {fmt(row.recommended, row.digits)}
                  </span>
                </div>
              ))}
            </div>

            {/* 모델 판정 */}
            <div className="rounded-md bg-zinc-50 border border-zinc-200 p-3 space-y-1">
              <p className="text-xs font-semibold text-zinc-600">모델 판정</p>
              <p className="text-xs text-zinc-700">{data.judgment}</p>
            </div>

            {/* 미반영 요소 */}
            {data.unmodeled_factors.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs font-semibold text-zinc-600">
                  미반영 요소
                </p>
                <ul className="list-disc list-inside space-y-0.5">
                  {data.unmodeled_factors.map((f, i) => (
                    <li key={i} className="text-xs text-zinc-500">
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
