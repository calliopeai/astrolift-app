import * as Sentry from "@sentry/nextjs";
import { scrubUrlSecrets } from "./lib/url-secret-telemetry";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.NODE_ENV,
  tracesSampleRate: 1.0,
  beforeSend: scrubUrlSecrets,
  beforeSendTransaction: scrubUrlSecrets,
  enabled: !!process.env.NEXT_PUBLIC_SENTRY_DSN,
});
