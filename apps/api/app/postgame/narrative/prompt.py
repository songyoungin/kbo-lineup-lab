"""System and user prompts for the postgame narrative LLM call."""

from __future__ import annotations

import json

from app.postgame.narrative.types import NarrativeFacts

SYSTEM_PROMPT = (
    "당신은 KBO 경기 후 라인업 분석을 쓰는 야구 칼럼니스트다. "
    "주어진 사실만으로, 모델의 사전 예측과 실제 경기 결과 사이의 긴장을 중심으로 "
    "한국어로 딱 한 문단(4~6문장)의 이야기를 쓴다. 구성은 도입(모델이 예상한 것) → "
    "긴장(실제 선택이 어떻게 달랐는지) → 결과(누가 모델을 증명하거나 반증했는지)다. "
    "주어지지 않은 수치나 사건을 지어내지 말고, 분석적이고 중립적인 어조를 유지하라."
)


def build_user_prompt(facts: NarrativeFacts) -> str:
    """Serialise the facts into a compact JSON block for the model."""
    payload = {
        "gap_label": facts.gap_label,
        "pregame_actual_score": f"{facts.pregame_actual_score:.2f}",
        "pregame_recommended_score": f"{facts.pregame_recommended_score:.2f}",
        "overperformers": [
            {"name": p.name, "score": p.performance_score, "box": p.box_line}
            for p in facts.overperformers
        ],
        "underperformers": [
            {"name": p.name, "score": p.performance_score, "box": p.box_line}
            for p in facts.underperformers
        ],
        "difference_reviews": [
            {
                "batting_order": d.batting_order,
                "actual": d.actual_name,
                "recommended": d.recommended_name,
                "verdict": d.verdict,
                "actual_performance": d.actual_performance,
                "recommended_performance": d.recommended_performance,
            }
            for d in facts.difference_reviews
        ],
    }
    return "다음 경기 사실을 바탕으로 한 문단 이야기를 써라:\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )
