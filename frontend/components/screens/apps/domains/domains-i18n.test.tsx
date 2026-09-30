import { readFileSync } from "node:fs";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { AddDomainSheet } from "./AddDomainSheet";
import { UploadCertSheet } from "./UploadCertSheet";
import { CertExpiryBadge, DomainHandshakeCard } from "./DomainHandshakeCard";
import {
  ADD_SHEET,
  DOMAIN_ACTIVE,
  ENV_PROD,
  HANDSHAKE_CARD,
  UPLOAD_SHEET,
  WORKLOADS,
} from "./app-domains.fixtures";
import { useAppDomains } from "./use-app-domains";
const authority = vi.hoisted(() => ({ granted: true }));
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: toasts }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => authority.granted, loading: false }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/apps/storefront/domains",
  useRouter: () => ({ replace: vi.fn() }),
}));
const locales = ["en", "es", "fr", "de", "pt-BR", "ja", "ko", "zh-Hans"];
const catalog = (locale: string) => JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
beforeEach(() => {
  authority.granted = true;
  vi.clearAllMocks();
});
it.each(locales)(
  "%s adds DNS-TXT domains and uploads PEM unchanged through translated sheets",
  async (locale) => {
    const messages = catalog(locale);
    const t = createTranslator({ locale, messages, namespace: "apps.domains" });
    const add = vi.fn(async () => true);
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <AddDomainSheet {...ADD_SHEET} onSubmit={add} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(/_astrolift-challenge\.<hostname>/)).toBeVisible();
    fireEvent.change(screen.getByLabelText(t("addSheet.hostname")), {
      target: { value: " Checkout.Example.COM " },
    });
    fireEvent.click(screen.getByRole("button", { name: t("addSheet.submit") }));
    await waitFor(() =>
      expect(add).toHaveBeenCalledWith("checkout.example.com", "dns_txt", false, "")
    );
    view.unmount();
    const upload = vi.fn(async () => true);
    const cert = "-----BEGIN CERTIFICATE-----\ndemo-public-cert\n-----END CERTIFICATE-----";
    const key =
      "-----BEGIN PRIVATE KEY-----\ndemo-non-secret-test-value\n-----END PRIVATE KEY-----";
    const certView = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <UploadCertSheet {...UPLOAD_SHEET} onSubmit={upload} />
      </NextIntlClientProvider>
    );
    fireEvent.change(screen.getByLabelText(t("upload.certLabel")), { target: { value: cert } });
    fireEvent.change(screen.getByLabelText(t("upload.keyLabel")), { target: { value: key } });
    fireEvent.click(screen.getByRole("button", { name: t("upload.submit") }));
    await waitFor(() => expect(upload).toHaveBeenCalledWith(cert, key));
    certView.unmount();
  }
);
it.each(locales)(
  "%s preserves DNS copies and actual routing values behind translated controls",
  async (locale) => {
    const messages = catalog(locale);
    const t = createTranslator({ locale, messages, namespace: "apps.domains" });
    const write = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: write },
    });
    const save = vi.fn(async () => true);
    const domain = {
      ...DOMAIN_ACTIVE,
      edgeAuthState: "ungated",
      pathRoutes: [],
      redirectRules: [],
    };
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <DomainHandshakeCard {...HANDSHAKE_CARD} domain={domain} onSavePathRoutes={save} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(t("cert.edgeAuthUngated"))).toHaveAttribute(
      "title",
      t("cert.edgeAuthUngatedHelp")
    );
    const record = domain.requiredDnsRecords[0];
    const label = t("cert.recordLabel", { kind: record.kind, label: t("cert.dnsColumns.value") });
    fireEvent.click(screen.getByRole("button", { name: t("copy.action", { label }) }));
    expect(write).toHaveBeenCalledWith(record.value);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: t("routing.addRoute") }));
    fireEvent.change(screen.getByLabelText(t("routing.pathPrefix")), {
      target: { value: "/api/raw" },
    });
    fireEvent.change(screen.getByLabelText(t("routing.targetPort")), { target: { value: "8087" } });
    fireEvent.change(screen.getByLabelText(t("routing.priority")), { target: { value: "3" } });
    await user.click(screen.getByRole("combobox", { name: t("routing.workload") }));
    await user.click(screen.getByRole("option", { name: WORKLOADS[0].slug }));
    await user.click(screen.getByLabelText(t("routing.stripPrefix")));
    await user.click(screen.getByRole("button", { name: t("routing.addToList") }));
    await user.click(screen.getByRole("button", { name: t("routing.save") }));
    expect(save).toHaveBeenCalledWith([
      expect.objectContaining({
        pathPrefix: "/api/raw",
        targetWorkloadSlug: WORKLOADS[0].slug,
        targetPort: 8087,
        priority: 3,
        stripPrefix: true,
      }),
    ]);
    view.unmount();
  }
);
it.each(locales)(
  "%s renders plural certificate expiry without inventing unavailable dates",
  (locale) => {
    const messages = catalog(locale);
    const t = createTranslator({ locale, messages, namespace: "apps.domains.cert" });
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <CertExpiryBadge
          expiresAt={new Date(Date.now() + 6.5 * 86400000).toISOString()}
          status="ok"
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(t("expires", { days: 6 }))).toBeVisible();
    view.rerender(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <CertExpiryBadge expiresAt="invalid" status="ok" />
      </NextIntlClientProvider>
    );
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
    expect(view.container).toBeEmptyDOMElement();
    view.rerender(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <CertExpiryBadge expiresAt={null} status="failed" />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(t("renewalFailed"))).toBeVisible();
    view.unmount();
  }
);
it.each(locales)(
  "%s preserves actual wildcard and certificate mutation envelopes and server errors",
  async (locale) => {
    const messages = catalog(locale);
    const t = createTranslator({ locale, messages, namespace: "apps.domains.toasts" });
    const requests: { name: string; variables: Record<string, unknown> }[] = [];
    let denied = false;
    const client = new ApolloClient({
      cache: new InMemoryCache(),
      link: new ApolloLink(
        (operation) =>
          new Observable((observer) => {
            requests.push({ name: operation.operationName ?? "", variables: operation.variables });
            queueMicrotask(() => {
              let data: Record<string, unknown>;
              if (operation.operationName === "ListAppDomains")
                data = { astroliftAppDomains: [DOMAIN_ACTIVE] };
              else if (operation.operationName === "ListEnvironments")
                data = { astroliftEnvironments: [ENV_PROD] };
              else if (operation.operationName === "ListWorkloads")
                data = { astroliftWorkloads: WORKLOADS };
              else {
                const key =
                  operation.operationName === "AddWildcardDomain"
                    ? "addWildcardDomain"
                    : "uploadCustomDomainCertificate";
                data = {
                  [key]: {
                    ok: !denied,
                    errors: denied
                      ? [{ code: "PERMISSION_DENIED", message: "Owner grant required" }]
                      : [],
                    data: denied ? null : DOMAIN_ACTIVE,
                  },
                };
              }
              observer.next({ data });
              observer.complete();
            });
          })
      ),
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ApolloProvider client={client}>
        <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
          {children}
        </NextIntlClientProvider>
      </ApolloProvider>
    );
    const hook = renderHook(() => useAppDomains("storefront"), { wrapper });
    await waitFor(() => expect(hook.result.current.domains).toHaveLength(1));
    await act(async () =>
      expect(
        await hook.result.current.addDomain(
          "tenant.example.com",
          "http_01",
          true,
          "arn:aws:acm:demo"
        )
      ).toBe(true)
    );
    expect(requests.find((r) => r.name === "AddWildcardDomain")?.variables).toEqual({
      input: {
        appSlug: "storefront",
        hostname: "tenant.example.com",
        validationMethod: "dns_01",
        sniCertRef: "arn:aws:acm:demo",
      },
    });
    expect(toasts.success).toHaveBeenCalledWith(
      t("addedWildcard", { hostname: "tenant.example.com" })
    );
    await act(async () =>
      expect(
        await hook.result.current.uploadCertificate(DOMAIN_ACTIVE, "demo-cert-pem", "demo-test-key")
      ).toBe(true)
    );
    expect(requests.find((r) => r.name === "UploadCustomDomainCertificate")?.variables).toEqual({
      input: {
        id: DOMAIN_ACTIVE.id,
        certificatePem: "demo-cert-pem",
        privateKeyPem: "demo-test-key",
      },
    });
    denied = true;
    await act(async () =>
      expect(
        await hook.result.current.uploadCertificate(DOMAIN_ACTIVE, "demo-cert-pem", "demo-test-key")
      ).toBe(false)
    );
    expect(toasts.error).toHaveBeenLastCalledWith("Owner grant required");
    hook.unmount();
    client.stop();
  }
);
it("keeps translated domain mutations hidden when deployment permission is absent", () => {
  authority.granted = false;
  const messages = catalog("es");
  const t = createTranslator({ locale: "es", messages, namespace: "apps.domains" });
  const view = render(
    <NextIntlClientProvider locale="es" messages={messages} timeZone="UTC">
      <DomainHandshakeCard {...HANDSHAKE_CARD} />
    </NextIntlClientProvider>
  );
  expect(screen.queryByRole("button", { name: t("routing.addRoute") })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: t("routing.addRedirect") })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: t("cert.recheck") })).not.toBeInTheDocument();
  view.unmount();
});
