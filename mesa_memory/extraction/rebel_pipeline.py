import logging
from typing import List, Dict

try:
    from transformers import pipeline
except ImportError:
    pipeline = None

logger = logging.getLogger("MESA_Extraction")

class RebelExtractor:
    def __init__(self, model_name: str = "Babelscape/rebel-large"):
        self.model_name = model_name
        self._pipeline = None
        
    def _initialize(self):
        if self._pipeline is None:
            if pipeline is None:
                raise ImportError("transformers library is not installed")
            logger.info(f"Initializing REBEL extraction pipeline with model {self.model_name}")
            self._pipeline = pipeline("text2text-generation", model=self.model_name)
            
    def extract_triplets(self, text: str) -> List[Dict[str, str]]:
        if not text.strip():
            return []
            
        self._initialize()
        
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
            logger.error(f"REBEL extraction failed: {e}")
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
