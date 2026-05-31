"""Prompts for the batting-order LLM call (static system + dynamic user)."""

from __future__ import annotations

from app.lineup_model.player_score import compute_player_score
from app.lineup_model.types import Handedness, HitterStats, Position

# Static prefix: identical across games, so it is a prompt-caching target.
# Always sent ahead of the dynamic user prompt. Content is Korean on purpose:
# the product requires Korean rationales and summaries.
SYSTEM_PROMPT = (
    "당신은 KBO 타순을 설계하는 야구 분석가입니다. 주어진 9명의 선수를 "
    "1번부터 9번 타순으로 배치하세요.\n\n"
    "목표: 팀 전체의 득점을 극대화합니다. 출루율이 높은 테이블세터를 1~2번에 두어 "
    "득점 생산형(장타·타점) 타자가 주자가 있는 상황에서 타석에 들어서게 하세요. "
    "팀에서 가장 뛰어난 타자는 보통 3~4번(클린업)에 배치하며, 1번(리드오프)에 "
    "소모하지 않습니다.\n\n"
    "규칙:\n"
    "- 제공된 9명의 player_id만 사용하고, 각 선수를 정확히 한 번만 배치합니다.\n"
    "- batting_order는 1부터 9까지의 순열이어야 합니다.\n"
    "- 존재하지 않는 선수나 스탯을 지어내지 마세요.\n"
    "- 각 선수의 배치 근거(rationale_ko)와 전체 요약(lineup_summary_ko)은 "
    "반드시 자연스러운 한국어로 작성합니다.\n"
    "- 반드시 제공된 JSON 스키마 형식으로만 응답합니다."
)


def build_user_prompt(
    assigned: dict[Position, HitterStats],
    opp_handedness: Handedness,
) -> str:
    """Build the dynamic user prompt carrying each assigned player's stats.

    Args:
        assigned: Mapping of position to the assigned HitterStats.
        opp_handedness: Opposing starter's handedness.

    Returns:
        The user prompt string to send to the LLM.
    """
    lines: list[str] = [
        f"상대 선발 투수 손: {opp_handedness}",
        "",
        "선발 라인업 9명(점수는 결정론 엔진이 계산한 참고용 종합 점수):",
    ]
    for position, stats in assigned.items():
        breakdown = compute_player_score(stats, position, opp_handedness)
        score = breakdown.total_score if breakdown is not None else 0.0
        lines.append(
            f"- player_id={stats.player_id} 포지션={position} 타격손={stats.handedness} "
            f"OBP={stats.obp:.3f} SLG={stats.slg:.3f} OPS={stats.ops:.3f} "
            f"최근14일OPS={stats.recent_14d_ops} 최근30일OPS={stats.recent_30d_ops} "
            f"vsLHP_OPS={stats.vs_lhp_ops}(PA={stats.vs_lhp_pa}) "
            f"vsRHP_OPS={stats.vs_rhp_ops}(PA={stats.vs_rhp_pa}) "
            f"종합점수={score:.4f}"
        )
    return "\n".join(lines)
