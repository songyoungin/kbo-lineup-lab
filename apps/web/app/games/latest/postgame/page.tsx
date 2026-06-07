import { redirect } from "next/navigation";
import { fetchLatestGameId } from "@/lib/latest-game";

/** Redirects to the postgame page of the most recent game (home when unknown). */
export default async function LatestPostgamePage() {
  const gameId = await fetchLatestGameId();
  redirect(gameId != null ? `/games/${gameId}/postgame` : "/");
}
