# Kaynakla doğrulanan mimari

Başlangıç SHA 8ddd2dc04cf697fe237e7a49e391543c01182d83; inceleme envanterden sonra yapıldı.

SQLite canonical katalog/assertion/mutation/outbox sahipliği; LanceDB ve Kùzu derived projections. V4 combined tek writer; v3 split topology ayrı root. Kaynak: mesa_memory/runtime_entrypoint.py, mesa_memory/api/server.py, mesa_storage/dao.py.

V4 arama dört lane: vector, BM25, assertion, graph. mesa_storage/retrieval_scope.py:8–96: k=60 ve dört ağırlık 1.0. dao.py:6179–6211 lexical +200/+40 lane sıralama içindir; 6620 RRF; 6732–6800 bounded legal factor. Graph çağrısı 20 seed, 3 hop, min(500,max(limit*10,50)) sonuç sınırı. provider frontier 64, fanout 32, timeout 15s.

MesaV4Client.insert title zorunlu, legal_identity parametresi yok; HTTP destekler. wait_until_committed terminal failure durumunda da döner; COMMITTED kontrolü zorunlu. API search default 10 ve 1..50; context default 2048, single mode.

/health ve /metrics auth ister; /health/init public. OpenAPI/docs/redoc auth dependency kapsamı dışında; reverse proxy ağ politikası gerekir.

Model varsayılan disabled; configured local Magibu 768d, extraction Ollama qwen3:1.7b; gerçek yüklü/operational model kanıtı değildir. Dış embedding text-embedding-3-small 1536 boyut ayrıca seçilmeli. Mode 2 iki distinct validator ister; varsayılan model-enabled iken 2.

E2E repository salt okunur SHA 3b7562cb65800cde27877dc3a7cd505a70822bf8 ve temiz durum gözlendi. README sealed certification ownership doğruluyor. MESA_Data checkout yok. Resmî TEST çalıştırılmadı.
