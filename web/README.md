# Airgap dashboard

Next.js reader for the live queue, audit trail, and policy table.

There is **no approve control**. Verdicts are produced on the device. This app
holds the broker `ui` token on the server (BFF) and never exposes it to page JS.

## Run

```bash
cd web
npm install
set AIRGAP_BROKER_URL=http://127.0.0.1:8741
set AIRGAP_UI_TOKEN=<ui token>
set AIRGAP_CSRF_SECRET=<optional; otherwise read from GET /pending cookie>
npm run dev
```

Binds `127.0.0.1:3000`. Live updates come from same-origin SSE that polls the
broker every second — the broker has no event stream.

## Verify

```bash
npm run test
npm run lint
npm run build
```
