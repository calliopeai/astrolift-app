import { MockedProvider } from "@apollo/client/testing/react";
import type { Decorator, Preview } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import * as React from "react";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ConfirmProvider } from "@/hooks/use-confirm";
import messages from "@/messages/en.json";
import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";
import { PermissionsProvider } from "@/providers/PermissionsProvider";

import "../app/globals.css";

/**
 * One decorator for every story: the providers the app wraps each page in,
 * mocked once here rather than per story (spec 44 §8), and the appearance
 * axes as toolbar controls, stamped on <html> the way the app's boot script
 * does, so a story can be checked in every ground, accent and density.
 */
function AppProviders({
  globals,
  children,
}: {
  globals: Record<string, string>;
  children: React.ReactNode;
}) {
  const { ground, accent, density, mode, permissions } = globals;
  const granted = React.useMemo(
    () => new Set<string>(permissions === "none" ? [] : ASTROLIFT_PERMISSIONS),
    [permissions]
  );
  React.useEffect(() => {
    const root = document.documentElement;
    root.dataset.ground = ground;
    root.dataset.accent = accent;
    root.dataset.density = density;
    root.classList.toggle("dark", mode === "dark");
  }, [ground, accent, density, mode]);
  return (
    <NextIntlClientProvider locale="en" messages={messages}>
      <MockedProvider mocks={[]}>
        <PermissionsProvider value={{ granted, loading: false }}>
          <TooltipProvider>
            <ConfirmProvider>
              <div className="bg-background text-foreground min-h-screen p-6 font-sans">
                {children}
              </div>
            </ConfirmProvider>
          </TooltipProvider>
        </PermissionsProvider>
      </MockedProvider>
    </NextIntlClientProvider>
  );
}

const withAppProviders: Decorator = (Story, context) => (
  <AppProviders globals={context.globals as Record<string, string>}>
    <Story />
  </AppProviders>
);

const toolbar = (title: string, items: string[]) => ({
  description: title,
  toolbar: { title, items, dynamicTitle: true },
});

const preview: Preview = {
  decorators: [withAppProviders],
  globalTypes: {
    mode: toolbar("Mode", ["dark", "light"]),
    ground: toolbar("Ground", ["black", "charcoal", "emerald", "paper", "mist"]),
    accent: toolbar("Accent", ["green", "copper", "ice", "periwinkle", "amber"]),
    density: toolbar("Density", ["compact", "cards"]),
    permissions: toolbar("Permissions", ["all", "none"]),
  },
  initialGlobals: {
    mode: "dark",
    ground: "black",
    accent: "green",
    density: "compact",
    permissions: "all",
  },
  parameters: {
    layout: "fullscreen",
    a11y: { test: "error" },
  },
};

export default preview;
