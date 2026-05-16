import logging
import re

from mesa_memory.config import config

logger = logging.getLogger("MESA_Retrieval")

try:
    import spacy

    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    logger.warning(
        "spaCy is not installed. QueryAnalyzer will fall back to basic regex extraction."
    )


def normalize_query(query: str) -> str:
    return " ".join(query.strip().split()).lower()


class QueryAnalyzer:
    def __init__(self):
        self.nlp = None
        if SPACY_AVAILABLE:
            model_name = config.spacy_language_model
            try:
                self.nlp = spacy.load(model_name)
            except OSError:
                logger.warning(
                    f"spaCy model '{model_name}' is not installed. "
                    "QueryAnalyzer will fall back to basic regex extraction. "
                    f"To fix, run: python -m spacy download {model_name}"
                )

    def extract_entities(self, query: str) -> list[str]:
        normalized = normalize_query(query)

        if self.nlp is not None:
            doc = self.nlp(normalized)
            entities = list({ent.text.strip() for ent in doc.ents if ent.text.strip()})
            if entities:
                return entities

            nouns = list(
                {
                    token.text.strip()
                    for token in doc
                    if token.pos_ in ("NOUN", "PROPN") and token.text.strip()
                }
            )
            if nouns:
                return nouns

            tokens = [
                token.text.strip()
                for token in doc
                if not token.is_stop and not token.is_punct and token.text.strip()
            ]
            return tokens if tokens else [normalized]
        else:
            tokens = [t for t in re.split(r"\W+", normalized) if len(t) > 2]
            stop_words = {
                "the",
                "is",
                "at",
                "which",
                "on",
                "and",
                "a",
                "an",
                "in",
                "to",
                "for",
                "of",
                "with",
                "that",
            }
            filtered_tokens = list({t for t in tokens if t not in stop_words})
            return filtered_tokens if filtered_tokens else [normalized]
