import { render, screen, cleanup } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import de from "@/messages/de.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";
import { ModelSubscriptionUsagePanel } from "./ModelSubscriptionUsagePanel";
import { observation } from "./model-observations.fixtures";
import { subscriptionUsageReceipt } from "@/graphql/models/subscription-usage.hooks";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
const locales = { en, es, de, fr, ja, ko, "pt-BR": pt, "zh-Hans": zh };
const model = sharedModelDetailProps.model!;
const data = {
  serviceId: model.id,
  clusterId: model.clusterId,
  subscriptionId: "subscription",
  scope: "authenticated_subscription",
  start: "2026-10-03T12:00:00Z",
  end: "2026-10-03T12:15:00Z",
  retrievedAt: "2026-10-03T12:15:01Z",
  stepSeconds: 30,
  metrics: [
    observation("requests_per_second", "requests/s", 0),
    observation("error_requests_per_second", "requests/s", 0),
    observation("response_bytes_per_second", "bytes/s", 1024),
    observation("latency_p95", "seconds", 0.25),
  ].map((m) => ({ ...m, source: "authenticated_model_subscription" })),
};
afterEach(cleanup);
describe("truthful app-subscription traffic presentation", () => {
  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "renders %s exact scope and usage boundaries with escaped app identity",
    (locale) => {
      const messages = locales[locale],
        t = messages.models.shared.subscriptionUsage;
      render(
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          <ModelSubscriptionUsagePanel
            appSlug="<script>foreign</script>"
            environmentName="production"
            read={{ data, loading: false, stale: false, error: null }}
            onRefresh={() => {}}
            onClose={() => {}}
          />
        </NextIntlClientProvider>
      );
      expect(
        screen.getByRole("heading", { name: `${t.title} · <script>foreign</script> / production` })
      ).toBeInTheDocument();
      for (const copy of [
        t.scope,
        t.bytes,
        t.unsupported,
        t.boundary,
        t.requests,
        t.errors,
        t.responseBytes,
        t.latency,
      ])
        expect(screen.getByText(copy)).toBeInTheDocument();
      expect(screen.getAllByText("0")).toHaveLength(2);
      expect(document.querySelector("script")).toBeNull();
      expect(Object.keys(t).sort()).toEqual(Object.keys(en.models.shared.subscriptionUsage).sort());
      if (locale !== "en")
        expect(messages.models.shared.hosting.adminRequired).not.toEqual(
          en.models.shared.hosting.adminRequired
        );
      else
        expect(messages.models.shared.hosting.adminRequired).toBe(
          "Only an active platform super-admin can host or configure models."
        );
    }
  );
  it("admits only exact subscription metric provenance and refuses missing/duplicate measures", () => {
    expect(subscriptionUsageReceipt(data, model, "subscription")).toBe(true);
    for (const changes of [
      { subscriptionId: "foreign" },
      { clusterId: "foreign" },
      { serviceId: "foreign" },
      { scope: "deployment_aggregate_not_app_attributed" },
      { start: "invalid" },
      { stepSeconds: 0 },
      { metrics: [] },
      { metrics: [...data.metrics, data.metrics[0]] },
      { metrics: data.metrics.map((m) => ({ ...m, source: "vllm" })) },
      { metrics: data.metrics.map((m) => ({ ...m, value: -1 })) },
      { metrics: data.metrics.map((m) => ({ ...m, unit: "percent" })) },
    ])
      expect(subscriptionUsageReceipt({ ...data, ...changes }, model, "subscription")).toBe(false);
  });
});
