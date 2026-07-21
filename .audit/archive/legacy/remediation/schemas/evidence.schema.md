# Evidence Schema

Kanıt türleri: `static`, `failing_test`, `passing_test`, `regression_test`, `integration`, `runtime`, `staging`, `diff`, `command`, `resource`, `manual_decision`.

Her evidence kaydı `EV-WXXX-NNN` ID’si, wave, finding, komut/test, sonuç, evidence level ve path taşır. Ham çıktılar secret içermemeli; büyük çıktılar maskelenmiş özet ve SHA-256 ile temsil edilebilir. E3/E4 ancak gerçek isolated runtime/staging çıktısıyla iddia edilir.
