import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import {
  CHECKOUT,
  COMPARISON,
  DANA,
  DIAGNOSIS_LONG,
  DIAGNOSIS_NO,
  DIAGNOSIS_YES,
  LONG,
  LONG_PRINCIPAL,
  LONG_SCOPE_TREE,
  SAM,
  SCOPE_TREE,
} from "@/components/access/fixtures";

import { diagnosticsProps, ME } from "./fixtures";
import { PermissionsDiagnosticsView } from "./PermissionsDiagnosticsView";

/**
 * Check access (design 3.7), on the Diagnostics route it replaces: "Can
 * <who> <do what> on <which>?", the resolver's reasoning as the answer, and
 * Compare for two people.
 */
const meta: Meta = {
  title: "Screens/Administration/Permissions/PermissionsDiagnosticsView",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const TREE = { roots: SCOPE_TREE, loading: false, error: null, onRetry: () => {} };
const answer = (diagnosis: typeof DIAGNOSIS_YES) => ({
  diagnosis,
  loading: false,
  error: null,
  onRetry: () => {},
});

/** Dana can deploy checkout: Yes, with the binding that carries it. */
export const Full: Story = {
  render: () => (
    <PermissionsDiagnosticsView
      {...diagnosticsProps({
        who: DANA,
        permission: "app.deploy",
        scope: CHECKOUT,
        scopeTree: TREE,
        explainer: answer(DIAGNOSIS_YES),
      })}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole("status")).toHaveTextContent("Yes.");
  },
};

/** The default: the viewer, nothing asked yet. */
export const Empty: Story = {
  render: () => <PermissionsDiagnosticsView {...diagnosticsProps({ scopeTree: TREE })} />,
};

/** A No, with the failing step. */
export const Denied: Story = {
  render: () => (
    <PermissionsDiagnosticsView
      {...diagnosticsProps({
        who: SAM,
        permission: "secret.read",
        scopeTree: TREE,
        explainer: answer(DIAGNOSIS_NO),
      })}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <PermissionsDiagnosticsView
      {...diagnosticsProps({
        me: null,
        meLoading: true,
        who: null,
        permission: "app.deploy",
        scopeTree: { ...TREE, roots: [], loading: true },
        explainer: { diagnosis: null, loading: true, error: null, onRetry: () => {} },
      })}
    />
  ),
};

/** The diagnosis failed (asking about someone else without being a superuser). */
export const Error: Story = {
  render: () => (
    <PermissionsDiagnosticsView
      {...diagnosticsProps({
        who: DANA,
        permission: "app.deploy",
        scopeTree: { ...TREE, roots: [], error: { message: "Network error: failed to fetch" } },
        explainer: {
          diagnosis: null,
          loading: false,
          error: { message: "You may only diagnose your own permissions." },
          onRetry: () => {},
        },
      })}
    />
  ),
};

/** Compare two people: only A, only B, both. */
export const Compare: Story = {
  render: () => (
    <PermissionsDiagnosticsView
      {...diagnosticsProps({
        comparing: true,
        who: ME,
        other: DANA,
        compare: { comparison: COMPARISON, loading: false, error: null, onRetry: () => {} },
      })}
    />
  ),
};

/** Compare with no second person picked yet. */
export const CompareEmpty: Story = {
  render: () => <PermissionsDiagnosticsView {...diagnosticsProps({ comparing: true })} />,
};

export const CompareError: Story = {
  render: () => (
    <PermissionsDiagnosticsView
      {...diagnosticsProps({
        comparing: true,
        other: SAM,
        compare: {
          comparison: null,
          loading: false,
          error: { message: "Only superusers may compare permissions." },
          onRetry: () => {},
        },
      })}
    />
  ),
};

const LONG_PROPS = diagnosticsProps({
  who: { ...LONG_PRINCIPAL, kind: "user" },
  permission: `app.${LONG}`,
  scope: { kind: "APP", id: "app-long", name: LONG },
  scopeTree: { ...TREE, roots: LONG_SCOPE_TREE },
  explainer: answer(DIAGNOSIS_LONG),
});

export const LongStrings: Story = { render: () => <PermissionsDiagnosticsView {...LONG_PROPS} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <PermissionsDiagnosticsView {...LONG_PROPS} />
    </div>
  ),
};
