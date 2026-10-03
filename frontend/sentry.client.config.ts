import * as Sentry from "@sentry/nextjs";
import {
  excludePrivateTransfer,
  privateTransferSpan,
  PRIVATE_TRANSFER_QUERY,
} from "./lib/private-transfer-telemetry";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.NODE_ENV,
  tracesSampleRate: 1.0,
  replaysOnErrorSampleRate: 1.0,
  replaysSessionSampleRate: 0.1,
  beforeBreadcrumb: excludePrivateTransfer,
  beforeSend: excludePrivateTransfer,
  beforeSendTransaction: excludePrivateTransfer,
  beforeSendSpan: privateTransferSpan,
  integrations: [
    Sentry.replayIntegration({
      maskAllText: true,
      maskAllInputs: true,
      networkCaptureBodies: false,
      networkDetailDenyUrls: [PRIVATE_TRANSFER_QUERY],
      beforeAddRecordingEvent: excludePrivateTransfer,
    }),
  ],
  enabled: !!process.env.NEXT_PUBLIC_SENTRY_DSN,
});
