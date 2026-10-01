import { act, renderHook } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import { accessCrumbs, grantHref, PEOPLE_HREF } from "./access-nav";
import { useGrantPage } from "./use-grant-page";

const navigation = vi.hoisted(() => ({ push: vi.fn(), params: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => navigation.params,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));
// Page tests own routing/notification only; the real Apollo hook has separate HTTP tests.
vi.mock("@/components/access/use-grant-access", () => ({
  useGrantAccess: () => ({ expirySupported: true }),
}));

describe.each(Object.entries({ es, fr, ja }))("%s grant page", (locale, messages) => {
  it("localizes actual crumb switches without changing destinations, active entries or query identities", () => {
    const principal = {
      kind: "group" as const,
      id: "okta:literal:identity",
      name: "<Actual>{Group}",
    };
    const scope = {
      kind: "APP",
      id: "30000000-0000-0000-0000-000000000003",
      name: "<Actual>{App}",
    };
    navigation.params = new URLSearchParams(
      grantHref({ principal, scope, returnTo: "/apps/actual/access" }).split("?")[1]
    );
    const wrapper = ({ children }: { children: ReactNode }) => (
      <NextIntlClientProvider locale={locale} messages={messages}>
        {children}
      </NextIntlClientProvider>
    );
    const { result } = renderHook(useGrantPage, { wrapper });
    const t = createTranslator({ locale, messages, namespace: "shared.access.grant" });
    expect(result.current.initialDraft).toEqual({ principals: [principal], scope });
    expect(result.current.crumbs.map((crumb) => crumb.label)).toEqual([
      t("admin"),
      t("access"),
      t("people"),
      t("title"),
    ]);
    for (const [index, crumb] of result.current.crumbs.entries()) {
      expect(crumb.switcher?.map(({ href, active }) => ({ href, active }))).toEqual(
        accessCrumbs("people", t("title"))[index].switcher?.map(({ href, active }) => ({
          href,
          active,
        }))
      );
    }
    expect(result.current.crumbs[0].switcher?.map((option) => option.label)).toEqual([
      t("links.organization"),
      t("access"),
      t("links.projects"),
    ]);
    expect(result.current.crumbs[1].switcher?.map((option) => option.label)).toEqual([
      t("people"),
      t("links.teams"),
      t("links.roles"),
      t("links.policies"),
      t("links.check"),
    ]);
    navigation.push.mockClear();
    vi.mocked(toast.success).mockClear();
    act(() => result.current.onDone([{ ok: true }, { ok: false }, { ok: true }]));
    expect(toast.success).toHaveBeenCalledExactlyOnceWith(t("success", { count: 2 }));
    expect(navigation.push).toHaveBeenCalledExactlyOnceWith("/apps/actual/access");
  });

  it("keeps cancellation free of success feedback and refuses an external return URL", () => {
    navigation.params = new URLSearchParams("return=//foreign.example/path");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <NextIntlClientProvider locale={locale} messages={messages}>
        {children}
      </NextIntlClientProvider>
    );
    const { result } = renderHook(useGrantPage, { wrapper });
    navigation.push.mockClear();
    vi.mocked(toast.success).mockClear();
    act(() => result.current.onCancel());
    expect(navigation.push).toHaveBeenCalledExactlyOnceWith(PEOPLE_HREF);
    expect(toast.success).not.toHaveBeenCalled();
  });
});
