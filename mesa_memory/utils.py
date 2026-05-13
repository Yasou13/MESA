import re

def _strip_markdown_json(text: str) -> str:
    """Strip markdown code fences from LLM JSON responses."""
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if match:
        return match.group(1).strip()
    return text.strip()
