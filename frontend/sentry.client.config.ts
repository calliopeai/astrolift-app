import * as Sentry from "@sentry/nextjs";
import {
  excludePrivateTransfer,
  privateTransferSpan,
  PRIVATE_TRANSFER_QUERY,
} from "./lib/private-transfer-telemetry";
import { scrubUrlSecrets } from "./lib/url-secret-telemetry";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.NODE_ENV,
  tracesSampleRate: 1.0,
  replaysOnErrorSampleRate: 1.0,
  replaysSessionSampleRate: 0.1,
  beforeBreadcrumb: (record) => scrubUrlSecrets(excludePrivateTransfer(record)),
  beforeSend: (record) => scrubUrlSecrets(excludePrivateTransfer(record)),
  beforeSendTransaction: (record) => scrubUrlSecrets(excludePrivateTransfer(record)),
  beforeSendSpan: privateTransferSpan,
  integrations: [
    Sentry.replayIntegration({
      maskAllText: true,
      maskAllInputs: true,
      networkCaptureBodies: false,
      networkDetailDenyUrls: [PRIVATE_TRANSFER_QUERY],
      beforeAddRecordingEvent: (record) => scrubUrlSecrets(excludePrivateTransfer(record)),
    }),
  ],
  enabled: !!process.env.NEXT_PUBLIC_SENTRY_DSN,
});
