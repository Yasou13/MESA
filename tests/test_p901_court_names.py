"""Court names must not activate Constitution ranking factors."""

import pytest

from mesa_storage.legal_identity import LegalEntityResolver


@pytest.mark.parametrize("law_names", [None, {"Anayasa": "2709"}])
@pytest.mark.parametrize(
    "court",
    ["Anayasa Mahkemesi", "Anayasa Mahkemesinin", "Anayasa Mahkemesi'nin",
     "ANAYASA MAHKEMESİNİN", "AYM"],
)
def test_constitutional_court_is_not_a_statute(court, law_names):
    resolver = LegalEntityResolver(law_names=law_names)
    assert resolver.extract_citations(f"{court} 10. madde hakkında ne karar verdi?") == []
    assert resolver.extract_citations(f"10. madde uyarınca {court} karar verir.") == []


@pytest.mark.parametrize("law_names", [None, {"Anayasa": "2709"}])
def test_court_does_not_hide_a_separate_real_citation(law_names):
    resolver = LegalEntityResolver(law_names=law_names)
    assert [(c.statute_code, c.article) for c in resolver.extract_citations(
        "Anayasa Mahkemesinin Anayasa m. 10 ve TBK m. 49 hakkındaki kararı"
    )] == [("Anayasa", "10"), ("TBK", "49")]
    assert [(c.statute_code, c.article) for c in resolver.extract_citations(
        "AYM, AY m. 10 hükmünü uyguladı."
    )] == [("Anayasa", "10")]
