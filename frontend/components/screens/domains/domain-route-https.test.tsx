import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { ManagedDomainDetail, type ManagedDomainDetailProps } from "./ManagedDomainDetail";
import { MANAGED_DOMAIN } from "./domains-environments.fixtures";

const copy = en.managedDomains;
const host = "storefront.apps.acme.example";

function view(props: ManagedDomainDetailProps) {
  return (
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <ManagedDomainDetail {...props} />
    </NextIntlClientProvider>
  );
}

describe("route TLS observation (#2287)", () => {
  it("runs a live HTTPS check for the route's hostname and shows its outcome there", () => {
    const onProbe = vi.fn().mockResolvedValue(undefined);
    const onResetProbe = vi.fn();
    const props: ManagedDomainDetailProps = {
      ...MANAGED_DOMAIN,
      initialTab: "routing",
      onProbe,
      onResetProbe,
    };
    const { rerender } = render(view(props));

    fireEvent.click(screen.getByRole("button", { name: copy.checkHttps }));
    expect(onResetProbe).toHaveBeenCalled();
    expect(onProbe).toHaveBeenCalledWith(
      expect.objectContaining({ hostname: host, tool: "HTTPS" })
    );

    rerender(
      view({
        ...props,
        probe: {
          state: "OK",
          perspective: "control_plane_public_ip",
          checkedAt: "2026-10-11T00:00:00Z",
          reason: "TLS_VERIFIED_HTTP_STATUS_OBSERVED",
          hostname: host,
          tool: "HTTPS",
          recordType: "A",
          values: [],
          publicAddress: "203.0.113.10",
          httpStatus: 200,
          tlsVerified: true,
          latencyMs: 40,
        },
      })
    );
    expect(screen.queryByRole("button", { name: copy.checkHttps })).not.toBeInTheDocument();
    expect(screen.getByText(copy.ok)).toBeInTheDocument();
  });

  it("never claims TLS for a route before a check has run", () => {
    render(view({ ...MANAGED_DOMAIN, initialTab: "routing" }));
    expect(screen.getByRole("button", { name: copy.checkHttps })).toBeEnabled();
    expect(screen.queryByText(copy.ok)).not.toBeInTheDocument();
  });
});
