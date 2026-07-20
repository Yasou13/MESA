import logging
import re
from importlib import import_module
from types import ModuleType

from mesa_memory.config import config

logger = logging.getLogger("MESA_Retrieval")

spacy: ModuleType | None = None
_spacy_import_attempted = False


def _load_spacy_module() -> ModuleType | None:
    """Load the optional spaCy package only when entity analysis is requested."""
    global _spacy_import_attempted, spacy
    if spacy is not None:
        return spacy
    if _spacy_import_attempted:
        return None
    _spacy_import_attempted = True
    try:
        spacy = import_module("spacy")
    except ImportError:
        logger.warning(
            "spaCy is not installed. QueryAnalyzer will fall back to basic regex extraction."
        )
    return spacy


def normalize_query(query: str) -> str:
    return " ".join(query.strip().split()).lower()


class QueryAnalyzer:
    def __init__(self):
        self.nlp = None
        spacy_module = _load_spacy_module()
        if spacy_module is None:
            return
        model_name = config.spacy_language_model
        try:
            self.nlp = spacy_module.load(model_name)
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
