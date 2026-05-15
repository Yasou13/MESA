def calculate_fitness_score(
    content: str, token_count: int, novelty_score: float = 1.0
) -> float:
    """Calculate the fitness score of a CMB candidate (0.0 - 1.0)."""
    word_count = len(content.split())

    word_density = word_count / token_count if token_count > 0 else 0.0
    density_norm = min(word_density / 1.0, 1.0)

    if 50 <= token_count <= 500:
        efficiency = 1.0
    elif token_count < 50:
        efficiency = max(0.1, token_count / 50.0)
    else:
        efficiency = max(0.1, 500.0 / token_count)

    score = (density_norm * 0.3) + (efficiency * 0.3) + (novelty_score * 0.4)
    return float(min(max(score, 0.0), 1.0))
