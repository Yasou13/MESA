import subprocess
import sys

import spacy

from mesa_memory.config import config


def normalize_query(query: str) -> str:
    return " ".join(query.strip().split()).lower()


class QueryAnalyzer:
    def __init__(self):
        model_name = config.spacy_language_model
        try:
            self.nlp = spacy.load(model_name)
        except OSError:
            subprocess.run(
                [sys.executable, "-m", "spacy", "download", model_name],
                check=True,
            )
            self.nlp = spacy.load(model_name)

    def extract_entities(self, query: str) -> list[str]:
        normalized = normalize_query(query)
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
