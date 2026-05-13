import json
from typing import Any, List, Optional, Type, Union
from pydantic import BaseModel
from mesa_memory.adapter.base import BaseUniversalLLMAdapter

class MockLLMAdapter(BaseUniversalLLMAdapter):
    def __init__(self, **kwargs):
        pass

    def complete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        prompt_lower = prompt.lower()
        
        # Handle decision prompts for Tier-3 validation
        if any(kw in prompt_lower for kw in ["conflict", "admit", "reject", "decision", "valence"]):
            res = '{"decision": "STORE", "justification": "Mocked validation"}'
            if schema:
                return schema.model_validate_json(res)
            return res

        # Handle Triplet Extraction prompts
        if any(kw in prompt_lower for kw in ["extract", "triplets", "json", "knowledge graph"]):
            if schema:
                res = {
                    "triplets": [
                        {
                            "record_index": 0,
                            "head": "Elon Musk",
                            "relation": "BUYS",
                            "tail": "Twitter",
                            "confidence": 0.99
                        }
                    ]
                }
                return schema.model_validate(res)
            # Fallback 1:1
            return '{"head": "Elon Musk", "relation": "BUYS", "tail": "Twitter"}'

        return "Mocked completion."

    async def acomplete(self, prompt: str, schema: Optional[Type[BaseModel]] = None, **kwargs) -> Union[str, BaseModel]:
        return self.complete(prompt, schema, **kwargs)

    def embed(self, text: str, **kwargs) -> list[float]:
        return [0.1] * 1536

    async def aembed(self, text: str, **kwargs) -> list[float]:
        return [0.1] * 1536

    def embed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]

    async def aembed_batch(self, texts: list[str], **kwargs) -> list[list[float]]:
        return [[0.1] * 1536 for _ in texts]

    def get_token_count(self, text: str) -> int:
        return len(text.split())

    async def extract_triplets(self, content: str) -> List[tuple[str, str, str]]:
        return [
            ("Elon Musk", "BUYS", "Twitter"),
            ("Morgan Stanley", "FINANCES", "Twitter Acquisition"),
            ("Tesla Collateral", "USED_FOR", "Margin Loans"),
            ("MSS Agents", "INFILTRATED", "Twitter Servers")
        ]

    def deep_research(self, query: str, context: list) -> str:
        return """# 🦅 MESA DEEP RESEARCH REPORT: Twitter (X) Acquisition & Security Crisis
        
## 📊 1. Baseline: The Hostile Takeover
Elon Musk'ın Twitter'ı devralma süreci 2022 başında gizli hisse alımlarıyla başladı. Pasif yatırımcı statüsünden (Schedule 13G) hızla aktif yatırımcı statüsüne (Schedule 13D) geçiş yapıldı. Yönetim kurulunun "Zehir Hapı" (Poison Pill) savunmasına rağmen, hissedar baskısı ve agresif finansman stratejisi 44 milyar dolarlık nihai satın almayı zorunlu kıldı.

## 🤖 2. Conflict: The Bot Ratio Dispute (mDAU)
Satın alma sürecindeki en büyük kriz, platformdaki spam ve sahte (bot) hesapların gerçek oranı etrafında patlak verdi. Twitter yönetimi SEC dosyalamalarında bu oranın mDAU'nun %5'inden az olduğunu yeminli beyanlarla iddia ederken; Musk'ın veri bilimcileri gerçek oranın %20'nin üzerinde olduğunu kanıtladı. Bu durum, "Önemli Olumsuz Etki" (MAE) gerekçesiyle anlaşmanın iptali için Delaware mahkemesine taşındı.

## 💰 3. Multi-Hop: The Morgan Stanley & Tesla Collateral
Satın almanın devasa finansman mimarisi çok katmanlıydı. Morgan Stanley liderliğindeki Tier-1 konsorsiyum, 13 milyar dolarlık kurumsal borç sağladı. Kritik nokta: Musk, devasa nakit ihtiyacını karşılamak için Tesla (TSLA) hisselerinin üçte birini (yaklaşık 238 milyon hisse) marj kredisi teminatı olarak rehin verdi. Faiz oranlarındaki artış, bankaları bu "asılı borcu" (hung debt) zararına taşımak zorunda bıraktı.

## 🛡️ 4. Security: The MSS Infiltration (Whistleblower)
Eski Güvenlik Şefi Peiter "Mudge" Zatko'nun ifşaatları, Twitter'ın iç altyapısının Çin İstihbaratı (MSS) ajanları tarafından sızıldığını ortaya koydu. Zayıf logging mekanizmaları ve FTC rıza kararnamesi ihlalleri, şirketin ulusal güvenlik açısından kritik bir zafiyet noktası olduğunu kanıtladı. Bu durum, Musk'ın anlaşmayı feshetme stratejisinin temel yasal dayanaklarından biri haline geldi.
"""
