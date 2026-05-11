import logging
import threading
from typing import List, Dict

try:
    from transformers import pipeline
except ImportError:
    pipeline = None

logger = logging.getLogger("MESA_Extraction")


# ---------------------------------------------------------------------------
# Singleton holder — ensures the 1.8 GB REBEL model is loaded exactly once
# per application lifecycle, regardless of how many RebelExtractor instances
# are created (e.g. by ConsolidationLoop re-init or test fixtures).
# ---------------------------------------------------------------------------

class _RebelModelHolder:
    """Thread-safe singleton for the REBEL transformers pipeline."""

    _instance = None
    _lock = threading.Lock()
    _pipeline = None

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def get_pipeline(self, model_name: str):
        """Return the cached pipeline, initializing on first call."""
        if self._pipeline is None:
            with self._lock:
                if self._pipeline is None:
                    if pipeline is None:
                        raise ImportError("transformers library is not installed")
                    logger.info(
                        "Initializing REBEL extraction pipeline with model %s "
                        "(singleton — will be reused for all subsequent calls)",
                        model_name,
                    )
                    self._pipeline = pipeline(
                        "text2text-generation", model=model_name
                    )
        return self._pipeline

    @classmethod
    def reset(cls):
        """Reset the singleton — intended for test teardown only."""
        with cls._lock:
            cls._pipeline = None
            cls._instance = None


_model_holder = _RebelModelHolder()


class RebelExtractor:
    def __init__(self, model_name: str = "Babelscape/rebel-large"):
        self.model_name = model_name

    @property
    def _pipeline(self):
        return _model_holder.get_pipeline(self.model_name)

    def extract_triplets(self, text: str) -> List[Dict[str, str]]:
        if not text.strip():
            return []

        # Max length for rebel-large is typically 256 or 512
        # Use truncation to ensure we don't crash on long texts
        try:
            extracted_text = self._pipeline(
                text, 
                return_tensors=False, 
                return_text=True, 
                max_new_tokens=128,
                truncation=True,
                max_length=256
            )
            raw_text = extracted_text[0]["generated_text"]
            return self._parse_rebel_output(raw_text)
        except Exception as e:
            logger.error(f"REBEL extraction failed: {e}", exc_info=True)
            return []

    def _parse_rebel_output(self, text: str) -> List[Dict[str, str]]:
        triplets = []
        subject, relation, object_ = '', '', ''
        text = text.strip()
        current = 'x'
        
        clean_text = text.replace("<s>", "").replace("<pad>", "").replace("</s>", "")
        
        for token in clean_text.split():
            if token == "<triplet>":
                current = 't'
                if relation != '':
                    triplets.append({'head': subject.strip(), 'relation': relation.strip(), 'tail': object_.strip()})
                    relation = ''
                subject = ''
            elif token == "<subj>":
                current = 's'
                if relation != '':
                    triplets.append({'head': subject.strip(), 'relation': relation.strip(), 'tail': object_.strip()})
                object_ = ''
            elif token == "<obj>":
                current = 'o'
                relation = ''
            else:
                if current == 't':
                    subject += ' ' + token
                elif current == 's':
                    object_ += ' ' + token
                elif current == 'o':
                    relation += ' ' + token
                    
        if subject != '' and relation != '' and object_ != '':
            triplets.append({'head': subject.strip(), 'relation': relation.strip(), 'tail': object_.strip()})
            
        return triplets
