import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class JudgeVerdict(BaseModel):
    """Strict wire contract for LLM-as-a-judge responses."""

    model_config = ConfigDict(extra="forbid", strict=True)

    is_correct: bool
    score: float = Field(ge=0.0, le=1.0)
    reasoning: str


_FENCED_JSON = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<payload>.*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


def parse_judge_verdict(raw: str) -> JudgeVerdict:
    """Accept exactly one JSON verdict, optionally in one complete code fence.

    This deliberately does not search prose for a JSON-looking substring: such
    extraction can silently judge the wrong object when a model emits examples,
    explanations, or multiple verdicts.
    """
    payload = raw.strip()
    fenced = _FENCED_JSON.fullmatch(payload)
    if fenced:
        payload = fenced.group("payload").strip()
    if not payload:
        raise ValueError("judge returned an empty payload")
    try:
        return JudgeVerdict.model_validate_json(payload)
    except ValidationError as exc:
        raise ValueError(
            "judge response is not one valid JudgeVerdict JSON object"
        ) from exc
