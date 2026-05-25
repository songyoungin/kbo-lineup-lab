// Fixture-shaped mock data for when the backend API is unavailable.
// Shape is structurally identical to a real TeamHomeResponse.

import type { TeamHomeResponse } from "./types";

export const MOCK_TEAM_HOME: TeamHomeResponse = {
  team_code: "LG",
  today: {
    game_id: 9001,
    game_date: "2026-05-25",
    opponent_team_code: "KT",
    venue: "잠실야구장",
    opponent_starter: null,
    pipeline_status: {
      schedule: "ok",
      lineup: "missing",
      stats: "ok",
      evaluation: "pending",
    },
  },
  recent: [
    {
      game_id: 9000,
      game_date: "2026-05-24",
      opponent_team_code: "SSG",
      verdict: "Nearly optimal",
    },
    {
      game_id: 8999,
      game_date: "2026-05-23",
      opponent_team_code: "SSG",
      verdict: "Acceptable",
    },
    {
      game_id: 8998,
      game_date: "2026-05-22",
      opponent_team_code: "두산",
      verdict: "Questionable",
    },
  ],
};
