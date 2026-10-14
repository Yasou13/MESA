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

- `/dashboard/showcase/`: bilingual implementation summary; ölçüm/benchmark sunmaz
- `/dashboard/showcase/visualizer/`: browser içi graph simülasyonu; API/model/persistence kanıtı değildir

Showcase ortak marka varlıklarını `/dashboard/brand/` yolundan kullanır.
Eski mock demo console kaldırılmıştır; “Open Console” bağlantıları gerçek
control-plane paneline (`/dashboard/`) gider.

Control API routes ve yetki için [Control panel](../docs/Mesa_Control_Panel.md); product kapsamı için [docs index](../docs/README.md).
