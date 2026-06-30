from pydantic import BaseModel


class EpistemicEvent(BaseModel):
    id: str
    timestamp: str
    fact: str
    source_document: str


class AdversarialScenario(BaseModel):
    id: str
    domain: str
    circuit_type: str
    context_t0: str
    context_t1: str
    question: str
    ground_truth_answer: str
    target_entity: str
