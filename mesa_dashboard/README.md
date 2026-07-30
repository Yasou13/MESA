# MESA Dashboard

React, TypeScript ve Vite tabanlı yerel MESA control-plane arayüzüdür.

## Yerel geliştirme

```bash
cd mesa_dashboard
npm ci
npm run dev
```

Vite, `/control`, `/v3` ve `/v4` isteklerini
`http://localhost:8000` adresine yönlendirir.

## Production build

```bash
cd mesa_dashboard
npm ci
npm run build
```

`scripts/run_server.py`, üretilen `dist/` dizinini `/dashboard/` altında sunar.

## Statik showcase

Önceki bağımsız demo içeriği dashboard’un public varlıkları altında tutulur:

- `/dashboard/showcase/`: ürün landing sayfası ve canlı RAG sandbox
- `/dashboard/showcase/visualizer/`: ingestion/knowledge-graph görselleştiricisi

Showcase ortak marka varlıklarını `/dashboard/brand/` yolundan kullanır.
Eski mock demo console kaldırılmıştır; “Open Console” bağlantıları gerçek
control-plane paneline (`/dashboard/`) gider.
