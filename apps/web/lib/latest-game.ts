import { ApiError, api } from "./api";

/**
 * Resolve the team's most recent game id from the team home endpoint.
 *
 * The API returns the most recent game as `today` and earlier games
 * (most-recent-first) as `recent`. Returns null when the API is
 * unreachable or no games exist yet.
 */
export async function fetchLatestGameId(): Promise<number | null> {
  try {
    const home = await api.teamHome();
    return home.today?.game_id ?? home.recent[0]?.game_id ?? null;
  } catch (e) {
    if (e instanceof ApiError) return null;
    throw e;
  }
}
