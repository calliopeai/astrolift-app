import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";
import { useWorkloadScaling } from "./use-workload-scaling";

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

beforeEach(() => vi.clearAllMocks());

describe("scaling feedback uses actual ICU translations", () => {
  it.each(locales)("interpolates success and fallback feedback in %s", async (locale) => {
    const messages = (await import(`../../../../messages/${locale}.json`)).default;
    const intlErrors: Error[] = [];
    let ok = true;
    const client = new ApolloClient({
      cache: new InMemoryCache(),
      link: new ApolloLink(
        (operation) =>
          new Observable((observer) => {
            observer.next({
              data:
                operation.operationName === "GetWorkloadScalingStatus"
                  ? { astroliftWorkloadScalingStatus: null }
                  : {
                      scaleAstroliftWorkload: {
                        ok,
                        errors: [],
                        data: ok
                          ? { workloadId: "workload", desiredReplicas: 4, readyReplicas: null }
                          : null,
                      },
                    },
            });
            observer.complete();
          })
      ),
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <NextIntlClientProvider
        locale={locale}
        messages={messages}
        timeZone="UTC"
        onError={(error) => intlErrors.push(error)}
      >
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
    const { result } = renderHook(
      () =>
        useWorkloadScaling({
          workloadId: "workload",
          appSlug: "app",
          workloadSlug: "web",
          environmentName: null,
          permission: { allowed: true, code: "", reason: "" },
          version: 3,
        }),
      { wrapper }
    );
    await act(async () => {
      expect(await result.current.onApply(4)).toBe(true);
    });
    const scaling = messages.apps.workloadDetail.scaling;
    expect(toast.success).toHaveBeenCalledWith(scaling.successToast.replace("{replicas}", "4"));
    ok = false;
    await act(async () => {
      expect(await result.current.onApply(4)).toBe(false);
    });
    expect(toast.error).toHaveBeenCalledWith(
      scaling.errorToast.replace("{message}", scaling.unknownError)
    );
    expect(intlErrors).toEqual([]);
    client.stop();
  });
});
