import { fireEvent, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { renderWithIntl, renderWithIntl as render } from "@/test/render-with-intl";
import { render as renderRaw } from "@testing-library/react";
import { fakeController } from "@/components/data-table/fixtures";
import { ManagedResourceCost } from "./ManagedResourceCost";
import { ManagedResourceDetail } from "./ManagedResourceDetail";
import { ProjectManagedResourceList } from "./ProjectManagedResourceList";
import { RESOURCE, RESOURCE_DETAIL } from "./resource-reads.fixtures";
import type { ManagedServiceCostPreview } from "./use-project-resources";

const PRICE: ManagedServiceCostPreview = {
  managedServiceId: RESOURCE.id,
  available: true,
  reason: "",
  message: "",
  monthlyTotal: 42.5,
  currency: "USD",
  pricingSourceUrl: "https://prices.example.test/redis",
  pricingFetchedAt: "2026-10-02T00:00:00Z",
  notes: [],
  approximate: false,
};

describe("truthful resource pricing", () => {
  it.each([
    { monthlyTotal: null },
    { monthlyTotal: Number.NaN },
    { monthlyTotal: Number.POSITIVE_INFINITY },
    { monthlyTotal: -1 },
    { available: false },
    { currency: "not-a-currency" },
    { pricingSourceUrl: "javascript:alert(1)" },
    { pricingSourceUrl: "http://prices.example.test" },
    { pricingFetchedAt: "" },
    { pricingFetchedAt: "unknown" },
  ])("keeps missing/invalid price evidence unavailable: %j", (change) => {
    render(<ManagedResourceCost preview={{ ...PRICE, ...change }} />);
    expect(screen.getByText("Pricing is unavailable.")).toBeVisible();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/\$0\.00/)).not.toBeInTheDocument();
  });
  it("shows an explicit zero only with usable source, currency and retrieval time", () => {
    render(<ManagedResourceCost preview={{ ...PRICE, monthlyTotal: 0 }} />);
    expect(screen.getByText("$0.00 per month")).toBeVisible();
    expect(screen.getByRole("link", { name: "Pricing source" })).toHaveAttribute(
      "href",
      PRICE.pricingSourceUrl
    );
  });
});

describe("exact metadata review", () => {
  it.each(["revision", "identity", "missing", "refused"])(
    "refuses %s before rendering writable details",
    (failure) => {
      const review = vi.fn();
      const current =
        failure === "missing"
          ? null
          : {
              ...RESOURCE,
              contextRevision: failure === "revision" ? "changed" : RESOURCE.contextRevision,
              id: failure === "identity" ? "01930000-0000-7000-8000-000000000099" : RESOURCE.id,
            };
      render(
        <ManagedResourceDetail
          {...RESOURCE_DETAIL}
          current={current}
          refused={failure === "refused"}
          onRefresh={review}
        />
      );
      expect(screen.getByRole("alert")).toHaveTextContent("context changed");
      expect(screen.queryByRole("button", { name: "Reprovision" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Attach" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Review again" }));
      expect(review).toHaveBeenCalledOnce();
    }
  );
  it("does not offer detach against consumers held from an in-flight page", () => {
    renderWithIntl(
      <ManagedResourceDetail
        {...RESOURCE_DETAIL}
        attachments={{ ...RESOURCE_DETAIL.attachments, isStale: true }}
      />
    );
    expect(screen.getByRole("button", { name: "Detach" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Attach" })).toBeDisabled();
  });
  it.each(["en", "es", "fr", "de", "ja", "ko", "pt-BR", "zh-Hans"])(
    "localizes known states without translating unknown provider data in %s",
    async (locale) => {
      const messages = (await import(`../../../messages/${locale}.json`)).default;
      const errors: Error[] = [];
      renderRaw(
        <NextIntlClientProvider
          locale={locale}
          messages={messages}
          timeZone="UTC"
          onError={(error) => errors.push(error)}
        >
          <ProjectManagedResourceList
            controller={fakeController({
              rows: [
                RESOURCE,
                {
                  ...RESOURCE,
                  id: "unknown-id",
                  status: "provider_phase_v2",
                  name: "literal-technical-name",
                },
              ],
            })}
            onOpen={() => {}}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(messages.projectResources.reads.statuses.active)).toBeVisible();
      expect(screen.getByText("provider_phase_v2")).toBeVisible();
      expect(screen.getByText("literal-technical-name")).toBeVisible();
      expect(errors).toEqual([]);
    }
  );
});
