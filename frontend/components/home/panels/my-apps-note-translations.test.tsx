import { renderHook } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import { useMyApps } from "./use-my-apps";
const read = vi.hoisted(() =>
  vi.fn(() => ({
    apps: [
      {
        slug: "checkout",
        name: "Actual name",
        latestDeployment: {
          status: "running",
          environmentName: "actual-prod",
          startedAt: "2026-09-30T10:00:00Z",
        },
      },
    ],
    total: 17,
    loading: false,
  }))
);
vi.mock("./home-reads", () => ({ useHomeMyApps: read }));
describe("Home Mine ownership note", () => {
  it.each([
    ["fr", fr],
    ["ja", ja],
  ] as const)(
    "uses the selected %s catalogue without changing the source read or observed app values",
    (locale, messages) => {
      const wrapper = ({ children }: PropsWithChildren) => (
        <NextIntlClientProvider locale={locale} messages={messages}>
          {children}
        </NextIntlClientProvider>
      );
      const { result } = renderHook(() => useMyApps(), { wrapper });
      expect(result.current.mineNote).toBe(messages.apps.list.views.mineNote);
      expect(result.current.mineNote).not.toContain("Mine means apps");
      expect(result.current.count).toBe(17);
      expect(result.current.items[0]).toEqual({
        slug: "checkout",
        name: "Actual name",
        environment: "actual-prod",
        deployedAt: "2026-09-30T10:00:00Z",
        status: "running",
      });
      expect(read).toHaveBeenCalledWith("top");
    }
  );
});
