# MESA Dashboard

React, TypeScript ve Vite tabanlı yerel MESA control-plane arayüzüdür.

## Yerel geliştirme

```bash
cd apps/control-dashboard
npm ci
npm run dev
```

Vite, `/control`, `/v3` ve `/v4` isteklerini
`http://localhost:8000` adresine yönlendirir.

## Production build

```bash
cd apps/control-dashboard
npm ci
npm run build
```

Canonical `mesa_runtime.app` uygulaması, üretilen `dist/` dizinini
`/dashboard/` altında sunar. Resmî `make package` komutu arayüzü derleyip
wheel içine ekler.

## Statik showcase

Önceki bağımsız demo içeriği dashboard’un public varlıkları altında tutulur:

- `/dashboard/showcase/`: ürün landing sayfası ve opt-in canlı RAG sandbox
- `/dashboard/showcase/visualizer/`: ingestion/knowledge-graph görselleştiricisi

Showcase ortak marka varlıklarını `/dashboard/brand/` yolundan kullanır.
Canlı sandbox yalnız development/test ortamında
`MESA_SHOWCASE_DEMO_ENABLED=true` ile etkinleşir.
Eski mock demo console kaldırılmıştır; “Open Console” bağlantıları gerçek
control-plane paneline (`/dashboard/`) gider.
