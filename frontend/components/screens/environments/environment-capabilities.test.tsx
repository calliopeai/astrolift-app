import { MockedProvider } from "@apollo/client/testing/react";
import { render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider, useTranslations } from "next-intl";
import { userEvent } from "storybook/test";
import { describe, expect, it } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import {
  ENV_SETTINGS,
  ENVIRONMENTS,
} from "@/components/screens/apps/settings/app-settings-members.fixtures";
import { EnvironmentSettingsView } from "@/components/screens/apps/settings/EnvironmentSettings";
import { IngressControlsView } from "@/components/screens/apps/settings/IngressControls";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import en from "@/messages/en.json";
import { PermissionsProvider } from "@/providers/PermissionsProvider";

import { ENV_PROD, ENV_STAGING_PAUSED, environmentsProps } from "./environments.fixtures";
import { localizedEnvironmentsList } from "./environments-list";
import { EnvironmentsScreen } from "./EnvironmentsScreen";

// Account-wide app.deploy is held throughout: each environment's own answer decides (#1867).
const DENIED: AstroliftAppEnvironment = { ...ENV_PROD, viewerPermissions: ["app.update"] };
const ALLOWED: AstroliftAppEnvironment = {
  ...ENV_STAGING_PAUSED,
  viewerPermissions: ["app.deploy"],
};

function Providers({ children }: { children: React.ReactNode }) {
  return (
    <MockedProvider>
      <PermissionsProvider
        value={{ granted: new Set(["app.deploy", "app.update"]), loading: false }}
      >
        <NextIntlClientProvider locale="en" messages={en}>
          <TooltipProvider>{children}</TooltipProvider>
        </NextIntlClientProvider>
      </PermissionsProvider>
    </MockedProvider>
  );
}

function Environments() {
  const t = useTranslations("lists.environments");
  const list = useLocalListState(localizedEnvironmentsList(t));
  return (
    <EnvironmentsScreen
      {...environmentsProps({ rows: [DENIED, ALLOWED], totalCount: 2 })}
      list={list}
    />
  );
}

async function menuItems(env: AstroliftAppEnvironment) {
  const row = screen.getByRole("row", { name: (name) => name.includes(env.url) });
  await userEvent.click(within(row).getByRole("button"));
  const items = screen.getAllByRole("menuitem").map((item) => item.textContent ?? "");
  await userEvent.keyboard("{Escape}");
  return items;
}

describe("environment actions follow each environment's viewerPermissions", () => {
  it("offers pause or resume only on the rows that allow app.deploy", async () => {
    render(
      <Providers>
        <Environments />
      </Providers>
    );
    const t = en.lists.environments;
    const denied = await menuItems(DENIED);
    expect(denied.some((text) => text.includes(t.pause) || text.includes(t.resume))).toBe(false);
    expect(await menuItems(ALLOWED)).toEqual(
      expect.arrayContaining([expect.stringContaining(t.resume)])
    );
  });

  it("offers the ingress toggle only where app.deploy is allowed", () => {
    render(
      <Providers>
        <IngressControlsView
          envs={[DENIED, ALLOWED]}
          loading={false}
          busyIds={[]}
          onToggle={async () => {}}
        />
      </Providers>
    );
    const t = en.apps.settings.ingress;
    expect(screen.queryByRole("button", { name: t.pause })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: t.resume })).toBeInTheDocument();
  });

  it("keeps an environment's overrides read-only where app.update is refused", () => {
    const t = en.apps.environmentOverrides;
    const { rerender } = render(
      <Providers>
        <EnvironmentSettingsView {...ENV_SETTINGS} />
      </Providers>
    );
    expect(screen.getByRole("button", { name: t.add })).toBeEnabled();
    rerender(
      <Providers>
        <EnvironmentSettingsView
          {...ENV_SETTINGS}
          envs={[{ ...ENVIRONMENTS[0], viewerPermissions: ["app.deploy"] }, ENVIRONMENTS[1]]}
        />
      </Providers>
    );
    expect(screen.getByRole("button", { name: t.add })).toBeDisabled();
  });
});
