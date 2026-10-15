# MESA Control Panel

Bu sayfa mevcut control-plane sözleşmesini açıklar. Önceki ZIP-temelli tasarım
önerisindeki endpoint ve ekran listeleri uygulanmış özellik kanıtı değildi.
Güncel kaynak `mesa_api/routers/control/router.py`, gateway
`mesa_mcp/gateway/`, repository katmanı `mesa_storage/control/` ve dashboard
`mesa_dashboard/src/` dizinlerindedir.

## Yetki ve erişim

API control router prefix'i `/control/mcp`'dir. Aktif API principal ve server-side
`ADMIN` rolü gerekir; credential özeti plaintext token içermez. Dashboard
remote deployment için authenticated reverse proxy arkasında çalıştırılmalıdır.
Gateway transport/binding credential ayrıca ayrı yetki sınırıdır. Loopback
browser erişimi API admin yetkisini otomatik vermez.

## Mevcut route'lar

| Method | `/control/mcp` altındaki path | İşlev |
|---|---|---|
| POST/GET | `/clients` | Client oluştur/listele |
| GET | `/clients/{client_id}` | Client ayrıntısı |
| PUT | `/clients/{client_id}/enabled` | Enable/disable (`enabled` query) |
| GET | `/clients/{client_id}/bindings` | Project/dataset bindings |
| GET | `/connections` | Aktif bağlantılar |
| GET/POST | `/settings` | Ayarlar; POST `key` ve `value` query alanları |
| GET/POST | `/policies` | Politika kuralları |
| GET | `/managed-clients` | Codex/Antigravity güvenli binding/credential özeti |
| POST | `/credentials/{credential_id}/revoke` | Credential iptali |
| GET | `/activity`, `/activity/{call_id}` | Bounded aktivite listesi/ayrıntısı |
| GET | `/approvals`, `/approvals/pending` | Onay listesi |
| POST | `/approvals/{approval_id}/decide` | Yetkili karar |
| GET | `/overview` | Aggregate durum |

Canlı OpenAPI, gereken parameter/schema'nın son kaynağıdır. Eski planın
`/control/memories`, genel mutation/processing queue UI, graph explorer,
export/import ve cost analytics endpoint'leri bu tablodan çıkarım yoluyla
var sayılamaz. Dashboard memory ekranı ilgili V4/session erişim yollarından
ayrı incelenmelidir; ekran adı yeni API garantisi değildir.

## Gateway ve onay yaşam döngüsü

Codex doğrudan HTTP MCP gateway'e; Antigravity binding-scoped stdio bridge ile
bağlanır. Gateway policy, credential, approval ve durable operation state'ini
saklar. Varsayılan write onayı işlem ID'siyle pending döner; uzun stdio çağrısını
açık tutmaz. `mesa operations approve/reject` yalnız geçerli pending operation,
aktif client/binding ve ADMIN principal için karar kaydeder. Gateway normal
consumer'ı onaylanmış işi yürütür. Aynı idempotency key aynı operation'ı döndürür.

Credential install, doctor, rotate, disable/uninstall ve protected XDG files
[MCP rehberinde](../README_MCP.md); kurulum/build
[dashboard README](../mesa_dashboard/README.md) içindedir.

## Kanıt sınırı

Aktivite kaydı context yanıtının teslim edildiğini gösterebilir; modelin o
bilgiyi gerçekten kullandığını veya cevabın doğruluğunu kanıtlamaz. Statik
showcase ve graph görselleştiricisi benchmark/sertifikasyon değildir. İnsan
onayı Core V4 `human_review` legal capability bayrağına eşit değildir.
