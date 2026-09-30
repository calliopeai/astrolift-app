import { MockedProvider } from "@apollo/client/testing/react";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { useState, type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { DEPLOY_DEPLOYING, DEPLOY_FAILED, DEPLOY_RUNNING } from "./app-deployments-logs.fixtures";
import {
  DeploymentActionDialog,
  type ActionTarget,
  type ConfirmedAction,
} from "./DeploymentRowActions";
import { useDeploymentActions } from "./use-deployment-actions";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const notices = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: notices }));
beforeEach(() => {
  notices.error.mockClear();
  notices.success.mockClear();
});
function Action({ kind }: { kind: ConfirmedAction }) {
  const deployment =
    kind === "abort" ? DEPLOY_DEPLOYING : kind === "discard" ? DEPLOY_FAILED : DEPLOY_RUNNING;
  const [target, setTarget] = useState<ActionTarget | null>({ kind, deployment });
  const actions = useDeploymentActions();
  return (
    <DeploymentActionDialog
      target={target}
      appSlug="storefront"
      actions={actions}
      onClose={() => setTarget(null)}
    />
  );
}
const cases = [
  [
    "abort",
    ABORT_DEPLOYMENT,
    "abortDeployment",
    "Interrompre le déploiement",
    "Motif de l’interruption",
  ],
  ["discard", ABORT_DEPLOYMENT, "abortDeployment", "Abandonner", "Motif de l’abandon"],
  ["redeploy", REDEPLOY_APP, "redeployApp", "Redéployer", null],
  ["rollback", ROLLBACK_DEPLOYMENT, "rollbackDeployment", "Restaurer", null],
] as const;
describe("translated deployment actions", () => {
  it.each(cases)(
    "%s keeps confirmation open on absent envelopes and server denial, then retries the same target",
    async (kind, query, field, label, reasonLabel) => {
      const deployment =
        kind === "abort" ? DEPLOY_DEPLOYING : kind === "discard" ? DEPLOY_FAILED : DEPLOY_RUNNING;
      const input = reasonLabel
        ? { id: deployment.id, reason: "user supplied reason" }
        : { id: deployment.id };
      const request = { query, variables: { input } };
      const empty = vi.fn(() => ({ data: reasonLabel ? { [field]: null } : {} }));
      const deny = vi.fn(() => ({
        data: {
          [field]: {
            ok: false,
            errors: [{ code: "OWNER_SCOPE_DENIED", message: "OWNER_SCOPE_DENIED", field: null }],
            data: null,
          },
        },
      }));
      const save = vi.fn(() => ({
        data: {
          [field]: {
            ok: true,
            errors: [],
            data: {
              ...deployment,
              status: reasonLabel ? "failed" : "running",
              ...(reasonLabel ? {} : { id: "fresh-rollout-id" }),
            },
          },
        },
      }));
      const onError = vi.fn();
      render(
        <MockedProvider
          mocks={[
            { request, result: empty, delay: 50 },
            { request, result: deny },
            { request, result: save },
          ]}
        >
          <PermissionsProvider
            value={{
              granted: new Set(["app.approve_deploy", "app.deploy", "app.rollback"]),
              loading: false,
            }}
          >
            <NextIntlClientProvider locale="fr" messages={fr} onError={onError}>
              <Action kind={kind} />
            </NextIntlClientProvider>
          </PermissionsProvider>
        </MockedProvider>
      );
      const dialog = screen.getByRole("alertdialog");
      expect(within(dialog).getByRole("heading")).toHaveTextContent(
        kind === "discard" ? deployment.imageTag.slice(0, 8) : deployment.environmentName
      );
      expect(empty).not.toHaveBeenCalled();
      if (reasonLabel) {
        fireEvent.click(within(dialog).getByRole("button", { name: label }));
        expect(within(dialog).getByRole("alert")).toHaveTextContent("Un motif est requis");
        expect(empty).not.toHaveBeenCalled();
        fireEvent.change(within(dialog).getByLabelText(reasonLabel), {
          target: { value: " user supplied reason " },
        });
      }
      fireEvent.click(within(dialog).getByRole("button", { name: label }));
      for (const button of within(dialog).getAllByRole("button")) expect(button).toBeDisabled();
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      await waitFor(() => expect(empty).toHaveBeenCalledTimes(1));
      await waitFor(() =>
        expect(notices.error).toHaveBeenCalledWith(expect.stringContaining("aucune réponse reçue"))
      );
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(notices.success).not.toHaveBeenCalled();
      if (reasonLabel)
        expect(within(dialog).getByLabelText(reasonLabel)).toHaveValue(" user supplied reason ");
      fireEvent.click(within(dialog).getByRole("button", { name: label }));
      await waitFor(() => expect(notices.error).toHaveBeenCalledWith("OWNER_SCOPE_DENIED"));
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(notices.success).not.toHaveBeenCalled();
      fireEvent.click(within(dialog).getByRole("button", { name: label }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(empty).toHaveBeenCalledTimes(1);
      expect(deny).toHaveBeenCalledTimes(1);
      expect(save).toHaveBeenCalledTimes(1);
      expect(notices.success).toHaveBeenCalledExactlyOnceWith(
        expect.stringContaining(reasonLabel ? "Échoué" : "En exécution")
      );
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it("reports a missing Japanese approval response without a success toast", async () => {
    const response = vi.fn(() => ({ data: { approveDeployment: null } }));
    function Wrapper({ children }: PropsWithChildren) {
      return (
        <MockedProvider
          mocks={[
            {
              request: {
                query: APPROVE_DEPLOYMENT,
                variables: { input: { id: DEPLOY_RUNNING.id } },
              },
              result: response,
            },
          ]}
        >
          <PermissionsProvider value={{ granted: new Set(["app.approve_deploy"]), loading: false }}>
            <NextIntlClientProvider locale="ja" messages={ja}>
              {children}
            </NextIntlClientProvider>
          </PermissionsProvider>
        </MockedProvider>
      );
    }
    const { result } = renderHook(() => useDeploymentActions(), { wrapper: Wrapper });
    expect(result.current.canApprove).toBe(true);
    expect(result.current.canDeploy).toBe(false);
    expect(result.current.canRollback).toBe(false);
    await act(async () => result.current.onApprove(DEPLOY_RUNNING));
    expect(notices.error).toHaveBeenCalledExactlyOnceWith("承認：応答を受信できませんでした");
    expect(notices.success).not.toHaveBeenCalled();
  });
  it.each(Object.entries(catalogs))(
    "%s resolves every action warning and outcome with its actual target arguments",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.apps.deployments.actions, onError });
      for (const key of Object.keys(en.apps.deployments.actions))
        expect(
          t(key as Parameters<typeof t>[0], {
            action: "ACTION",
            status: "STATE",
            env: "prod",
            app: "checkout-api",
            tag: "sha-123",
          })
        ).toBeTruthy();
      expect(t("redeployTitle", { tag: "sha-123", app: "checkout-api", env: "prod" })).toContain(
        "checkout-api/prod"
      );
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
