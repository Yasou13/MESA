"""Tests for mesa_storage.legal_rules module."""

import unittest

import pytest

from mesa_storage.legal_rules import RULES_VERSION, extract


@pytest.mark.parametrize(
    "text,docket,decision,date",
    [
        ("2022/1 E., 2023/2 K.", "2022/1", "2023/2", None),
        ("Anayasa Mahkemesi E. 2022/1, K. 2023/2", "2022/1", "2023/2", None),
        ("Y.C.G.K 2020/527 K", None, "2020/527", None),
        ("1.2.2023 tarihli ve 12-34 sayılı", "12", "34", "2023-02-01"),
        (
            "Bandırma Sulh Hukuk Mahkemesinde görülen 2009/183 Esas",
            "2009/183", None, None,
        ),
        ("２０２２/１ E., ２０２３/２ K.", "２０２２/１", "２０２３/２", None),
    ],
)
def test_decision_year_prefilter_keeps_all_reference_forms(text, docket, decision, date):
    items = extract(text)
    assert len(items) == 1
    item = items[0]
    assert item["type"] == "decision_ref"
    assert (item["docket_no"], item["decision_no"], item["date"]) == (
        docket, decision, date,
    )
    assert item["text"] == text[item["start"]:item["end"]] == text.rstrip(" ,.")


def test_dense_slash_citations_keep_spans_and_following_law_header():
    prefix = ("TCK/1 " * 5462)[:32768]
    suffix = " 6098 sayılı Türk Borçlar Kanununun 49 uncu maddesi"
    items = extract(prefix + suffix)
    assert len(items) == 5462
    for index, item in enumerate(items[:-1]):
        assert item == {
            "start": index * 6, "end": index * 6 + 5, "text": "TCK/1",
            "type": "citation", "law_no": None, "law_name": "TCK",
            "article": "1", "paragraph": None,
        }
    assert items[-1] == {
        "start": len(prefix) + 1, "end": len(prefix + suffix),
        "text": suffix.strip(), "type": "citation", "law_no": "6098",
        "law_name": "Türk Borçlar Kanunu", "article": "49", "paragraph": None,
    }


@pytest.mark.parametrize(
    "titlecase, uppercase, expected",
    [
        ("5 İş Günü", "5 İŞ GÜNÜ", {"type": "duration", "value": 5, "unit": "iş günü"}),
        ("15 Ekim 2023", "15 EKİM 2023", {"type": "date", "value": "2023-10-15"}),
        ("1 Nisan 2022", "1 NİSAN 2022", {"type": "date", "value": "2022-04-01"}),
        ("Ekim 2023", "EKİM 2023", {"type": "date", "value": "2023-10"}),
    ],
)
def test_turkish_uppercase_duration_and_dates(titlecase, uppercase, expected):
    for text in (titlecase, uppercase):
        items = extract(text)
        assert len(items) == 1
        assert {key: items[0][key] for key in expected} == expected
        assert text[items[0]["start"] : items[0]["end"]] == items[0]["text"] == text


@pytest.mark.parametrize(
    "titlecase, uppercase, court, chamber",
    [
        ("Yargıtay 3. Hukuk Dairesi", "YARGITAY 3. HUKUK DAİRESİ", "Yargıtay", "3. Hukuk Dairesi"),
        ("Danıştay 5. Dairesi", "DANIŞTAY 5. DAİRESİ", "Danıştay", "5. Dairesi"),
    ],
)
def test_turkish_uppercase_court_and_chamber(titlecase, uppercase, court, chamber):
    suffix = " 2022/1 E., 2023/2 K."
    baseline = extract(titlecase + suffix)
    assert len(baseline) == 1
    assert (baseline[0]["court"], baseline[0]["chamber"]) == (court, chamber)
    items = extract(uppercase + suffix)
    assert len(items) == 1
    item = items[0]
    assert item["court"] == court
    assert item["chamber"] is not None
    i_variants = str.maketrans("İı", "ii")
    assert item["chamber"].translate(i_variants).lower() == chamber.translate(i_variants).lower()
    assert item["docket_no"] == "2022/1"
    assert item["decision_no"] == "2023/2"
    assert (uppercase + suffix)[item["start"] : item["end"]] == item["text"]


class TestLegalRulesExtractor(unittest.TestCase):
    def test_rules_version(self):
        self.assertEqual(RULES_VERSION, "tr-legal-rules-3")

    def test_span_invariant(self):
        sample = (
            "Yargıtay 3. Hukuk Dairesi 2022/1235 E., 2022/1899 K. sayılı ilamı ile "
            "5237 sayılı TCK m. 141 uyarınca verilen 5.000 TL adli para cezasına ve "
            "iki yıl hapis cezasına ilişkin karar ONANMASINA 15/04/2023 tarihinde karar verildi."
        )
        items = extract(sample)
        self.assertGreater(len(items), 0)
        for item in items:
            start = item["start"]
            end = item["end"]
            self.assertEqual(sample[start:end], item["text"])
            self.assertIn("type", item)

    def test_empty_and_whitespace_text(self):
        self.assertEqual(extract(""), [])
        self.assertEqual(extract("   \n\t  "), [])

    def test_examples_per_type(self):
        # 1. decision_ref
        t_dec = "Yargıtay Hukuk Genel Kurulu 2022/1235 E., 2022/1899 K."
        items = extract(t_dec)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "decision_ref")
        self.assertEqual(items[0]["docket_no"], "2022/1235")
        self.assertEqual(items[0]["decision_no"], "2022/1899")

        # 2. citation
        t_cit = "6098 sayılı Türk Borçlar Kanununun 49 uncu maddesinin birinci fıkrası"
        items = extract(t_cit)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "citation")
        self.assertEqual(items[0]["law_no"], "6098")
        self.assertEqual(items[0]["article"], "49")
        self.assertEqual(items[0]["paragraph"], "1")

        # 3. date
        t_date = "Dava 15.04.2023 tarihinde açılmıştır."
        items = extract(t_date)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "date")
        self.assertEqual(items[0]["value"], "2023-04-15")

        # 4. amount
        t_amt = "Davalıdan 5.000 TL tahsiline karar verildi."
        items = extract(t_amt)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "amount")
        self.assertEqual(items[0]["value"], "5000")
        self.assertEqual(items[0]["currency"], "TRY")

        # 5. duration
        t_dur = "Sözleşme süresi iki yıl olarak kararlaştırıldı."
        items = extract(t_dur)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "duration")
        self.assertEqual(items[0]["value"], 2)
        self.assertEqual(items[0]["unit"], "yıl")

        # 6. outcome
        t_out = "Hükmün DÜZELTİLEREK ONANMASINA karar verildi."
        items = extract(t_out)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "outcome")
        self.assertEqual(items[0]["value"], "düzeltilerek onama")

    def test_error_cause_1_leading_date_in_citation(self):
        # Dates emitted inside a citation starting with <date> tarihli ve must not be separate dates
        sample = "21/1/2017 tarihli ve 6771 sayılı Kanunun 16 ncı maddesi uyarınca işlem yapılmıştır."
        items = extract(sample)
        # Citation should start at the date
        citations = [it for it in items if it["type"] == "citation"]
        dates = [it for it in items if it["type"] == "date"]
        self.assertEqual(len(citations), 1)
        self.assertTrue(citations[0]["text"].startswith("21/1/2017 tarihli ve 6771 sayılı"))
        self.assertEqual(citations[0]["law_no"], "6771")
        self.assertEqual(citations[0]["article"], "16")
        # Leading date must be suppressed from separate date extraction
        self.assertEqual(len(dates), 0)

    def test_error_cause_2_onay_is_not_duration(self):
        # 'onay' must not be recognized as 'on ay' (10 months)
        sample = "Bakanlık tarafından onay verilen her türlü masrafları kurumca ödenir."
        items = extract(sample)
        durations = [it for it in items if it["type"] == "duration"]
        self.assertEqual(durations, [])

    def test_error_cause_3_compound_number_words(self):
        # Compound number words written together (e.g. yirmibeş)
        sample1 = "Müebbet hapis cezasını gerektiren suçlarda yirmibeş yıl hapis cezası uygulanır."
        items1 = extract(sample1)
        durations = [it for it in items1 if it["type"] == "duration"]
        self.assertEqual(len(durations), 1)
        self.assertEqual(durations[0]["value"], 25)
        self.assertEqual(durations[0]["unit"], "yıl")
        self.assertEqual(durations[0]["text"], "yirmibeş yıl")

    def test_error_cause_4_comparative_durations(self):
        # Comparative durations per schema v1.3: item is number and unit only ('beş yıl', value 5)
        sample = "Beş yıldan fazla ve yirmi yıldan aşağı olmamak üzere hapis cezası verilir."
        items = extract(sample)
        durations = [it for it in items if it["type"] == "duration"]
        self.assertEqual(len(durations), 2)
        self.assertEqual(durations[0]["text"], "Beş yıl")
        self.assertEqual(durations[0]["value"], 5)
        self.assertEqual(durations[0]["unit"], "yıl")
        self.assertEqual(durations[1]["text"], "yirmi yıl")
        self.assertEqual(durations[1]["value"], 20)
        self.assertEqual(durations[1]["unit"], "yıl")

    def test_error_cause_5_written_out_and_glued_amounts(self):
        # Written-out amounts (yüz milyon lira, ikiyüzyirmi Türk Lirası) and glued 20.000,00-TL
        sample1 = "Bu fıkrada yer alan yüz milyon lira para cezası ibaresi değiştirilmiştir."
        items1 = extract(sample1)
        amts1 = [it for it in items1 if it["type"] == "amount"]
        self.assertEqual(len(amts1), 1)
        self.assertEqual(amts1[0]["value"], "100000000")
        self.assertEqual(amts1[0]["currency"], "TRY")

        sample2 = "İlgili fıkraya ikiyüzyirmi Türk Lirası idari para cezası eklenmiştir."
        items2 = extract(sample2)
        amts2 = [it for it in items2 if it["type"] == "amount"]
        self.assertEqual(len(amts2), 1)
        self.assertEqual(amts2[0]["value"], "220")
        self.assertEqual(amts2[0]["currency"], "TRY")

        sample3 = "Manevi tazminata yönelik davanın kısmen kabulü ile 20.000,00-TL tahsiline karar verildi."
        items3 = extract(sample3)
        amts3 = [it for it in items3 if it["type"] == "amount"]
        self.assertEqual(len(amts3), 1)
        self.assertEqual(amts3[0]["text"], "20.000,00-TL")
        self.assertEqual(amts3[0]["value"], "20000.00")
        self.assertEqual(amts3[0]["currency"], "TRY")


if __name__ == "__main__":
    unittest.main()
