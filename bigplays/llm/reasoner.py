from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from bigplays.config import settings
from bigplays.models import LLMJudgment


SYSTEM_PROMPT = (
    "You are a sports highlight editor. Judge whether a play is viral-worthy. "
    "Analyze context and produce: verdict (true/false), hype_score 0..1, tags, title, short rationale."
)


def get_model() -> Optional[ChatAnthropic]:
    if not settings.use_llm or not settings.anthropic_api_key:
        return None
    return ChatAnthropic(model=settings.llm_model, anthropic_api_key=settings.anthropic_api_key)


def judge_highlight(context_texts: List[str]) -> Optional[LLMJudgment]:
    model = get_model()
    if model is None:
        return None
    content = "\n\n".join(context_texts)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=(
            "Context:\n" + content + "\n\n" +
            "Respond in JSON with keys: verdict (bool), hype_score (0..1), tags (list of strings), title (string), rationale (string)."
        )),
    ]
    out = model.invoke(messages)
    text = out.content if isinstance(out.content, str) else str(out.content)
    # Best-effort parse without strict schema enforcement
    import json

    try:
        data = json.loads(text)
        return LLMJudgment(
            verdict=bool(data.get("verdict", False)),
            hype_score=float(data.get("hype_score", 0.0)),
            tags=[str(t) for t in (data.get("tags") or [])],
            title=str(data.get("title", ""))[:120],
            rationale=str(data.get("rationale", ""))[:500],
        )
    except Exception:
        return None

