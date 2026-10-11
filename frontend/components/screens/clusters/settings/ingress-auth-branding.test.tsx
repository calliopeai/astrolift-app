import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import en from "@/messages/en.json";
import { IngressAuthView } from "./IngressAuth";
import { INGRESS_AUTH } from "./fixtures";

const clientId = INGRESS_AUTH.existing!.user_pool_client_id;

function view(managedLoginBranding: string) {
  return render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <IngressAuthView
        {...INGRESS_AUTH}
        editing
        clients={[{ clientId, clientName: "astrolift-alb", managedLoginBranding }]}
      />
    </NextIntlClientProvider>
  );
}

describe("managed login branding warning (#1727)", () => {
  it.each([
    ["missing", en.clusterSettings.ingressAuth.brandingMissing],
    ["missing_quota_full", en.clusterSettings.ingressAuth.brandingQuotaFull],
  ])("warns for a %s client before saving", (status, copy) => {
    view(status);
    expect(screen.getByRole("alert")).toHaveTextContent(copy);
  });

  it.each(["present", "not_required", "unknown"])("stays quiet for %s", (status) => {
    view(status);
    expect(screen.queryByText(en.clusterSettings.ingressAuth.brandingMissing)).toBeNull();
    expect(screen.queryByText(en.clusterSettings.ingressAuth.brandingQuotaFull)).toBeNull();
  });
});
