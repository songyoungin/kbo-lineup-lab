import type {
  GeneratePostgameReviewRequest,
  GeneratePostgameReviewResponse,
  LineupComparisonResponse,
  PlayerComparisonResponse,
  PostgameResponse,
  PregameResponse,
  ReplayEvaluationRequest,
  ReplayEvaluationResponse,
  TeamHomeResponse,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError(res.status, `GET ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function apiPost<TReq, TRes>(
  path: string,
  body: TReq,
  init?: RequestInit
): Promise<TRes> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError(res.status, `POST ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<TRes>;
}

// Typed convenience wrappers for each API endpoint
export const api = {
  teamHome: () => apiGet<TeamHomeResponse>("/api/team/lg/home"),

  pregame: (gameId: number) =>
    apiGet<PregameResponse>(`/api/games/${gameId}/pregame`),

  lineupComparison: (gameId: number) =>
    apiGet<LineupComparisonResponse>(`/api/games/${gameId}/lineup-comparison`),

  playerCompare: (gameId: number, battingOrder: number) =>
    apiGet<PlayerComparisonResponse>(
      `/api/games/${gameId}/players/compare?batting_order=${battingOrder}`
    ),

  postgame: (gameId: number) =>
    apiGet<PostgameResponse>(`/api/games/${gameId}/postgame`),

  replayEvaluation: (body: ReplayEvaluationRequest) =>
    apiPost<ReplayEvaluationRequest, ReplayEvaluationResponse>(
      "/api/jobs/replay-evaluation",
      body
    ),

  generatePostgameReview: (body: GeneratePostgameReviewRequest) =>
    apiPost<GeneratePostgameReviewRequest, GeneratePostgameReviewResponse>(
      "/api/jobs/generate-postgame-review",
      body
    ),
};
