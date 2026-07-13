import re

# Domain ontology mapping canonical names to variations and abbreviations.
# This serves the same purpose as Cognee's ontology file but directly integrated.
ONTOLOGY = {
    # Laws
    "TBK": ["türk borçlar kanunu", "borçlar kanunu", "tbk", "b.k.", "bk"],
    "TMK": ["türk medeni kanunu", "medeni kanun", "tmk", "m.k.", "mk"],
    "TCK": ["türk ceza kanunu", "ceza kanunu", "tck"],
    "HMK": ["hukuk muhakemeleri kanunu", "hmk"],
    "TTK": ["türk ticaret kanunu", "ticaret kanunu", "ttk"],
    # Courts
    "Yargıtay Hukuk Genel Kurulu": [
        "yargıtay hukuk genel kurulu",
        "hukuk genel kurulu",
        "yhgk",
    ],
    "Yargıtay 4. Hukuk Dairesi": [
        "yargıtay 4. hukuk dairesi",
        "yargıtay 4. dairesi",
        "4. hukuk dairesi",
        "4.hd",
        "4. hd",
    ],
    "Yargıtay 11. Hukuk Dairesi": [
        "yargıtay 11. hukuk dairesi",
        "yargıtay 11. dairesi",
        "11. hukuk dairesi",
        "11.hd",
        "11. hd",
    ],
}


class LegalEntityResolver:
    """Ontology-based entity resolution for Turkish Legal Domain.

    Extracts canonical law names and courts from messy text, and maps
    law articles to their canonical graph node representation (e.g. 'TBK m.49').
    """

    def __init__(self):
        self.ontology = ONTOLOGY
        self.patterns = {}
        for canonical, aliases in self.ontology.items():
            alias_patterns = []
            for alias in aliases:
                # Use non-word boundaries to match whole words/phrases
                escaped = re.escape(alias)
                pattern = f"(?:(?<=\\W)|^){escaped}(?:(?=\\W)|$)"
                alias_patterns.append(pattern)
            self.patterns[canonical] = re.compile(
                "|".join(alias_patterns), re.IGNORECASE
            )

        # Regex to catch "m. 49", "madde 49", "md 49", "m.49"
        self.article_pattern = re.compile(r"(?:m\.|madde|md\.?)\s*(\d+)", re.IGNORECASE)
        self.laws = {"TBK", "TMK", "TCK", "HMK", "TTK"}

    def extract_entities(self, text: str) -> list[str]:
        """Extract and normalize legal entities from the text.

        Args:
            text: The raw user query.

        Returns:
            A list of canonical entity names suitable for graph seed lookup.
        """
        resolved = set()
        found_canonicals = []

        # 1. Resolve basic canonical names
        for canonical, pattern in self.patterns.items():
            if pattern.search(text):
                found_canonicals.append(canonical)
                resolved.add(canonical)

        # 2. Extract article numbers
        articles = self.article_pattern.findall(text)

        # 3. Combine laws and articles to match KuzuDB node naming convention: "TBK m.49"
        for canonical in found_canonicals:
            if canonical in self.laws:
                for art in articles:
                    resolved.add(f"{canonical} m.{art}")

        return list(resolved)
