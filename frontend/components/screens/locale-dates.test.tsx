import { RESOURCE, RESOURCE_DETAIL } from "./projects/resource-reads.fixtures";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import { RESOURCES } from "./projects/projects-detail.fixtures";
import { ProjectResourcesScreen } from "./projects/ProjectResourcesScreen";
import { TOKENS_SCREEN } from "./tokens/tokens.fixtures";
import { TOKENS_LIST } from "./tokens/tokens-list";
import { TokensScreen } from "./tokens/TokensScreen";

vi.mock("next/navigation", () => ({
  usePathname: () => "/tokens",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));

const at = "2026-09-30T23:30:00Z";

function TokenDates() {
  const list = useLocalListState(TOKENS_LIST);
  return (
    <TokensScreen
      {...TOKENS_SCREEN}
      list={list}
      totalCount={1}
      rows={[{ ...TOKENS_SCREEN.rows[0], createdAt: at, expiresAt: at, lastUsedAt: at }]}
      renderScopePicker={() => null}
    />
  );
}

describe("screen dates honor the configured locale and time zone", () => {
  it.each([
    ["fr", "UTC"],
    ["ja", "Asia/Tokyo"],
  ])("formats API key dates and project operations in %s / %s", async (locale, timeZone) => {
    const messages = (await import(`../../messages/${locale}.json`)).default;
    const errors: Error[] = [];
    const tree = render(
      <NextIntlClientProvider
        locale={locale}
        messages={messages}
        timeZone={timeZone}
        onError={(error) => errors.push(error)}
      >
        <TokenDates />
        <ProjectResourcesScreen
          {...RESOURCES}
          resourceDetail={{
            ...RESOURCE_DETAIL,
            target: RESOURCE,
            current: { ...RESOURCE, operationKind: "provision", operationCompletedAt: at },
          }}
        />
      </NextIntlClientProvider>
    );
    const day = new Intl.DateTimeFormat(locale, {
      year: "numeric",
      month: "short",
      day: "numeric",
      timeZone,
    }).format(new Date(at));
    expect(screen.getAllByText(day)).toHaveLength(2);
    const stamp = new Intl.DateTimeFormat(locale, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      timeZoneName: "short",
      timeZone,
    }).format(new Date(at));
    expect(screen.getByText(stamp)).toBeVisible();
    expect(
      screen.getByText(messages.projectResources.operation.completed.replace("{at}", stamp), {
        exact: false,
      })
    ).toBeVisible();
    expect(errors).toEqual([]);
    tree.unmount();
  });
});
