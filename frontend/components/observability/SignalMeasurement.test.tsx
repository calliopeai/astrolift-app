import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import type { AstroliftAppGoldenSignal } from "@/graphql/__generated__/schema";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { SignalMeasurement } from "./SignalMeasurement";

const locales = { en, de, es, fr, ja, ko, "pt-BR": pt, "zh-Hans": zh };
const signal: AstroliftAppGoldenSignal = {
  name: "SATURATION_CPU",
  unit: "ratio",
  rangeSeconds: 300,
  reason: "NO_DATA_YET",
  samples: [],
  promql: "",
  measurement: {
    effectiveScope: "WORKLOAD",
    source: "CADVISOR_KUBE_STATE_METRICS",
    identityBasis: "VERIFIED_RUNTIME",
    available: false,
    unavailableReason: "MISSING_LIMITS",
    target: {
      organizationId: "org-guid",
      appId: "app-guid",
      appSlug: "fixture",
      environmentId: "environment-guid",
      environmentName: "selected",
      clusterId: "cluster-guid",
      namespace: "recorded-namespace",
      workloadId: "workload-guid",
      workloadSlug: "api",
    },
    containers: [
      {
        podName: "api-pod",
        podUid: "pod-guid",
        containerName: "api",
        containerId: "physical-container",
      },
    ],
    usageSamples: [],
    limitSamples: [],
    measurementStart: "2026-10-01T12:00:00Z",
  },
};

describe("signal measurement provenance", () => {
  for (const [locale, messages] of Object.entries(locales)) {
    it(`shows canonical target and honest missing limits in ${locale}`, () => {
      render(
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          <SignalMeasurement signal={signal} />
        </NextIntlClientProvider>
      );
      const copy = messages.apps.observability.signalMeasurement;
      expect(screen.getByText(copy.reasons.MISSING_LIMITS)).toBeVisible();
      expect(screen.getByText(copy.scope.replace("{scope}", copy.scopes.WORKLOAD))).toBeVisible();
      expect(screen.getByText("selected · recorded-namespace · api")).toBeVisible();
      expect(screen.getByText("physical-container", { exact: false })).toBeInTheDocument();
      expect(screen.getByText("workload-guid")).toBeInTheDocument();
    });
  }
  it("does not infer workload scope from a legacy response", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <SignalMeasurement signal={{ ...signal, measurement: null }} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(en.apps.observability.signalMeasurement.unreported)).toBeVisible();
    expect(screen.queryByText("Scope: Workload")).not.toBeInTheDocument();
  });
});
