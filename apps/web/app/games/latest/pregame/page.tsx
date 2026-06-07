import { redirect } from "next/navigation";
import { fetchLatestGameId } from "@/lib/latest-game";

/** Redirects to the pregame page of the most recent game (home when unknown). */
export default async function LatestPregamePage() {
  const gameId = await fetchLatestGameId();
  redirect(gameId != null ? `/games/${gameId}/pregame` : "/");
}
