import { ApolloClient, ApolloLink, InMemoryCache, type Operation } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { Observable, type Subscriber } from "rxjs";
import { expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import messages from "@/messages/en.json";
import { DeployTokensScreen } from "./DeployTokensScreen";
import { TOKENS, TOKEN_REVEAL } from "./app-secrets-tokens.fixtures";
import { useDeployTokens } from "./use-deploy-tokens";

const scope = vi.hoisted(() => ({ orgId: "org", update: true }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: scope.orgId } }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    can: (p: string) => p !== "app.update" || scope.update,
    granted: new Set(["app.read"]),
    loading: false,
  }),
}));
vi.mock("@/components/PageShell", () => ({
  PageShell: ({ children, actions }: { children: ReactNode; actions: ReactNode }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
type Observer = Subscriber<{ data: Record<string, unknown> }>;
function done(observer: Observer, data: Record<string, unknown>) {
  observer.next({ data });
  observer.complete();
}
function setup() {
  scope.orgId = "org";
  scope.update = true;
  const metadata: Array<{ operation: Operation; observer: Observer }> = [];
  const rotations: Operation[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          if (operation.operationName === "GetAppDeployTokenRotationMetadata")
            metadata.push({ operation, observer });
          else if (operation.operationName === "RotateDeployToken") {
            rotations.push(operation);
            done(observer, { rotateDeployToken: { ok: true, errors: [], data: TOKEN_REVEAL } });
          } else
            done(observer, {
              astroliftAppDeployTokensPage: {
                items: TOKENS.map((t) => ({ ...t, __typename: "AstroliftDeployToken" })),
                nextCursor: null,
                hasNextPage: false,
                totalCount: TOKENS.length,
              },
            });
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <ApolloProvider client={client}>
        <TooltipProvider>{children}</TooltipProvider>
      </ApolloProvider>
    </NextIntlClientProvider>
  );
  function App({ slug = "storefront" }: { slug?: string }) {
    const props = useDeployTokens(slug);
    return <DeployTokensScreen {...props} tabs={null} />;
  }
  return { ...render(<App />, { wrapper }), client, App, metadata, rotations };
}
async function openRotate() {
  const buttons = await screen.findAllByRole("button", { name: "Rotate" });
  fireEvent.click(buttons[0]);
  return screen.getByRole("alertdialog");
}
function confirmation() {
  return within(screen.getByRole("alertdialog")).getByRole("button", { name: "Rotate token" });
}

it("waits for actual configured metadata and rotates only its loaded target", async () => {
  const t = setup();
  await openRotate();
  expect(confirmation()).toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("Reading the configured");
  expect(screen.queryByText(/Configured grace window:/)).not.toBeInTheDocument();
  fireEvent.click(confirmation());
  expect(t.rotations).toHaveLength(0);
  await waitFor(() => expect(t.metadata).toHaveLength(1));
  expect(t.metadata[0].operation.variables).toEqual({ appSlug: "storefront" });
  await act(async () =>
    done(t.metadata[0].observer, {
      astroliftAppDeployTokenRotationMetadata: { rotationGraceSeconds: 90 },
    })
  );
  expect(screen.getByText(/Configured grace window: 1m 30s/)).toBeVisible();
  expect(screen.getByText(/Rotation rechecks access/)).toBeVisible();
  fireEvent.click(confirmation());
  await waitFor(() => expect(t.rotations).toHaveLength(1));
  expect(t.rotations[0].variables).toEqual({ input: { id: TOKENS[0].id } });
  t.unmount();
  t.client.stop();
});

it.each([
  null,
  { rotationGraceSeconds: 0 },
  { rotationGraceSeconds: -1 },
  { rotationGraceSeconds: 1.5 },
])("blocks missing or invalid metadata %j and permits a fresh retry", async (value) => {
  const t = setup();
  await openRotate();
  await act(async () =>
    done(t.metadata[0].observer, { astroliftAppDeployTokenRotationMetadata: value })
  );
  expect(confirmation()).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("could not be read");
  fireEvent.click(confirmation());
  expect(t.rotations).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(t.metadata).toHaveLength(2));
  expect(confirmation()).toBeDisabled();
  await act(async () =>
    done(t.metadata[1].observer, {
      astroliftAppDeployTokenRotationMetadata: { rotationGraceSeconds: 10800 },
    })
  );
  expect(screen.getByText(/Configured grace window: 3h/)).toBeVisible();
  expect(confirmation()).toBeEnabled();
  t.unmount();
  t.client.stop();
});

it("refuses a failed read and rereads after close instead of admitting cached revoked metadata", async () => {
  const t = setup();
  await openRotate();
  await act(async () =>
    done(t.metadata[0].observer, {
      astroliftAppDeployTokenRotationMetadata: { rotationGraceSeconds: 10800 },
    })
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await openRotate();
  await waitFor(() => expect(t.metadata).toHaveLength(2));
  expect(confirmation()).toBeDisabled();
  await act(async () => t.metadata[1].observer.error(new Error("Permission denied")));
  expect(screen.getByRole("alert")).toHaveTextContent("could not be read");
  expect(confirmation()).toBeDisabled();
  expect(t.rotations).toHaveLength(0);
  t.unmount();
  t.client.stop();
});

it.each(["target", "app", "org"])(
  "ignores a delayed reply after closing/switching %s",
  async (change) => {
    const t = setup();
    await openRotate();
    const old = t.metadata[0];
    if (change === "target") {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      fireEvent.click(screen.getAllByRole("button", { name: "Rotate" })[1]);
    } else {
      if (change === "org") scope.orgId = "other-org";
      t.rerender(<t.App slug={change === "app" ? "another-app" : "storefront"} />);
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      await openRotate();
    }
    await waitFor(() => expect(t.metadata).toHaveLength(2));
    await act(async () =>
      done(old.observer, {
        astroliftAppDeployTokenRotationMetadata: { rotationGraceSeconds: 10800 },
      })
    );
    expect(confirmation()).toBeDisabled();
    expect(screen.queryByText(/Configured grace window:/)).not.toBeInTheDocument();
    await act(async () =>
      done(t.metadata[1].observer, {
        astroliftAppDeployTokenRotationMetadata: { rotationGraceSeconds: 60 },
      })
    );
    expect(confirmation()).toBeEnabled();
    expect(screen.getByText(/Configured grace window: 1m/)).toBeVisible();
    t.unmount();
    t.client.stop();
  }
);

it("gates all existing token writes on actual app.update instead of app.deploy", async () => {
  const t = setup();
  scope.update = false;
  t.rerender(<t.App />);
  await waitFor(() => expect(screen.getByText(TOKENS[0].name)).toBeVisible());
  expect(screen.queryByRole("button", { name: "Rotate" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create token" })).not.toBeInTheDocument();
  expect(t.metadata).toHaveLength(0);
  t.unmount();
  t.client.stop();
});
