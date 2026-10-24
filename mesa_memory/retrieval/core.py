def normalize_query(query: str) -> str:
    return " ".join(query.strip().split()).lower()
