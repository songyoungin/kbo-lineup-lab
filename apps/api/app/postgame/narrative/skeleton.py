"""Deterministic Korean narrative skeleton — the LLM-free fallback.

Pure function: identical NarrativeFacts always produce identical prose, so this
doubles as the testable core and the graceful-degradation output.
"""

from __future__ import annotations

from app.postgame.narrative.types import NarrativeFacts, NarrativePlayerLine

_GAP_LABEL_KO: dict[str, str] = {
    "nearly optimal": "최적에 가까운",
    "acceptable": "무난한",
    "questionable": "아쉬운",
    "low offensive efficiency": "비효율적인",
}


def _top(lines: tuple[NarrativePlayerLine, ...]) -> NarrativePlayerLine:
    # Deterministic: highest performance_score, name as tie-break.
    return sorted(lines, key=lambda p: (-p.performance_score, p.name))[0]


def build_skeleton(facts: NarrativeFacts) -> str:
    """Compose a deterministic one-paragraph Korean narrative from the facts."""
    gap_ko = _GAP_LABEL_KO.get(facts.gap_label, facts.gap_label)
    parts: list[str] = [
        f"모델은 이날 실제 라인업을 '{gap_ko}' 선택으로 평가했다"
        f"(실제 {facts.pregame_actual_score:.2f} vs 추천 "
        f"{facts.pregame_recommended_score:.2f} 기대득점)."
    ]

    if facts.overperformers:
        top = _top(facts.overperformers)
        parts.append(f"{top.name}, {top.performance_score:.1f}점으로 기대를 뛰어넘었다.")
    if facts.underperformers:
        low = _top(facts.underperformers)
        parts.append(f"반면 {low.name}, {low.performance_score:.1f}점에 그쳤다.")

    actual_won = sum(1 for d in facts.difference_reviews if d.verdict == "Actual choice succeeded")
    model_won = sum(
        1 for d in facts.difference_reviews if d.verdict == "Model would have done better"
    )
    if actual_won or model_won:
        if actual_won > model_won:
            parts.append(
                f"모델과 달랐던 선택 중 {actual_won}곳에서 실제 선택이 더 나은 결과를 냈다."
            )
        elif model_won > actual_won:
            parts.append(f"다만 모델이 추천한 선수가 {model_won}곳에서 더 나았을 것이다.")
        else:
            parts.append("모델과 실제의 선택은 엇갈린 결과로 우열을 가리지 못했다.")

    return " ".join(parts)
