import { StatusPill } from "@/components/status-pill";
import type { StatusTone } from "@/components/status-pill";
import { PERFORMANCE_LABEL_KO, PERFORMANCE_TONE } from "@/lib/i18n";
import type { PostgamePlayerLine } from "@/lib/types";

/**
 * box_line 객체를 슬래시 형식의 간결한 문자열로 포맷합니다.
 * 예: "2안타/4타수, 홈런, 2타점"
 */
function formatBoxLine(boxLine: Record<string, unknown>): string {
  const parts: string[] = [];

  const hits = boxLine["hits"] ?? boxLine["H"];
  const atBats = boxLine["at_bats"] ?? boxLine["AB"];
  if (hits !== undefined && atBats !== undefined) {
    parts.push(`${hits}안타/${atBats}타수`);
  }

  const hr = boxLine["home_runs"] ?? boxLine["HR"];
  if (hr && Number(hr) > 0) parts.push(`홈런 ${hr}`);

  const rbi = boxLine["rbi"] ?? boxLine["RBI"];
  if (rbi && Number(rbi) > 0) parts.push(`${rbi}타점`);

  const r = boxLine["runs"] ?? boxLine["R"];
  if (r && Number(r) > 0) parts.push(`${r}득점`);

  const bb = boxLine["walks"] ?? boxLine["BB"];
  if (bb && Number(bb) > 0) parts.push(`${bb}볼넷`);

  const so = boxLine["strikeouts"] ?? boxLine["SO"] ?? boxLine["K"];
  if (so && Number(so) > 0) parts.push(`${so}삼진`);

  if (parts.length === 0) return "기록 없음";
  return parts.join(", ");
}

/** 선수가 없을 때 보여주는 빈 상태 카드 */
function EmptyCard({ title, message }: { title: string; message: string }) {
  return (
    <div className="border border-zinc-200 rounded-md">
      <header className="border-b border-zinc-200 px-3 py-2">
        <span className="font-semibold text-sm">{title}</span>
      </header>
      <p className="px-3 py-4 text-xs text-zinc-400 text-center">{message}</p>
    </div>
  );
}

/**
 * 기대 이상/이하 선수 목록을 카드 형태로 표시합니다.
 */
export function PlayerOutcomeList({
  title,
  tone,
  players,
}: {
  title: string;
  tone: StatusTone;
  players: PostgamePlayerLine[];
}) {
  if (players.length === 0) {
    return <EmptyCard title={title} message="해당 선수 없음" />;
  }

  return (
    <div className="border border-zinc-200 rounded-md">
      <header className="border-b border-zinc-200 px-3 py-2 flex items-center justify-between">
        <span className="font-semibold text-sm">{title}</span>
        <StatusPill tone={tone}>{players.length}명</StatusPill>
      </header>
      <ul className="divide-y divide-zinc-100">
        {players.map((p) => (
          <li
            key={p.player_id}
            className="px-3 py-2 flex items-center justify-between gap-2"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-zinc-800 truncate">
                  {p.name}
                </span>
                <StatusPill tone={PERFORMANCE_TONE[p.label]}>
                  {PERFORMANCE_LABEL_KO[p.label]}
                </StatusPill>
              </div>
              <div className="text-xs text-zinc-500 mt-0.5">
                {formatBoxLine(p.box_line)}
              </div>
            </div>
            <span className="text-sm font-mono text-zinc-700 shrink-0">
              {p.performance_score.toFixed(1)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
