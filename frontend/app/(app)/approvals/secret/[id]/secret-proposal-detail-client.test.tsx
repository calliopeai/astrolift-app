import type { ReactNode } from "react";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";

import { SecretProposalDetailClient } from "./secret-proposal-detail-client";

// Mutable state the hoisted mocks read at call time — reset in beforeEach and
// overridden per case. ``vi.hoisted`` runs before the ``vi.mock`` factories, so
// they can safely close over it.
const state = vi.hoisted(() => ({
  proposal: null as AstroliftSecretChangeProposal | null,
  canApprove: false,
  mutationLoading: false,
}));

// Data + platform hooks are stubbed so the test exercises this client's own
// gating logic (status × permission → allowed actions, diff-summary fallback)
// rather than Apollo, i18n, routing, or the permission query.
vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({
    data: { astroliftSecretChangeProposal: state.proposal },
    loading: false,
    refetch: vi.fn(),
  }),
  useMutation: () => [vi.fn().mockResolvedValue({ data: {} }), { loading: state.mutationLoading }],
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// Return the translation key verbatim so assertions key off stable strings.
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/lib/i18n/formatters", () => ({
  useFormatters: () => ({
    locale: "en",
    formatDate: () => "now",
    formatDateTime: () => "now",
    formatNumber: (n: number) => String(n),
    formatPercent: (n: number) => String(n),
    formatCurrency: (n: number) => String(n),
    formatRelativeTime: () => "just now",
  }),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => state.canApprove }),
}));

// PageShell is page chrome (it reads app-chrome-context); render a thin shell
// so the test targets this client. The computed diff summary flows through the
// ``description`` prop, so surface it under a testid for assertion.
vi.mock("@/components/PageShell", () => ({
  PageShell: ({
    title,
    description,
    children,
  }: {
    title?: ReactNode;
    description?: ReactNode;
    children?: ReactNode;
  }) => (
    <div>
      <div data-testid="page-title">{title}</div>
      <div data-testid="page-description">{description}</div>
      {children}
    </div>
  ),
}));

function makeProposal(
  overrides: Partial<AstroliftSecretChangeProposal> = {},
): AstroliftSecretChangeProposal {
  return {
    id: "prop-1",
    registeredAppSlug: "billing-api",
    environmentName: "prod",
    op: "set",
    status: "pending",
    proposerUserId: "user-1",
    proposerDisplayName: "Proposer One",
    requiredApproverCount: 2,
    approvalsCount: 0,
    expiresAt: "2026-07-26T00:00:00Z",
    applyError: "",
    createdAt: "2026-07-25T00:00:00Z",
    payload: {},
    payloadDiff: { before: {}, after: { DATABASE_URL: "***" } },
    approvals: [],
    ...overrides,
  };
}

beforeEach(() => {
  state.proposal = makeProposal();
  state.canApprove = false;
  state.mutationLoading = false;
});

describe("SecretProposalDetailClient", () => {
  describe("action matrix (status × permission)", () => {
    it("shows approve + reject + withdraw when pending and the viewer can approve", () => {
      state.proposal = makeProposal({ status: "pending" });
      state.canApprove = true;
      render(<SecretProposalDetailClient proposalId="prop-1" />);

      expect(screen.getByRole("button", { name: "approve" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "reject" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "withdraw" })).toBeInTheDocument();
    });

    it("hides approve + reject but keeps withdraw when pending without approve permission", () => {
      state.proposal = makeProposal({ status: "pending" });
      state.canApprove = false;
      render(<SecretProposalDetailClient proposalId="prop-1" />);

      expect(screen.queryByRole("button", { name: "approve" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "reject" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "withdraw" })).toBeInTheDocument();
    });

    it("shows no decision actions once the proposal is no longer pending", () => {
      // A decided proposal (approved/rejected/applied) must not expose approve,
      // reject, or withdraw — even to a viewer who holds the approve permission.
      state.proposal = makeProposal({ status: "approved" });
      state.canApprove = true;
      render(<SecretProposalDetailClient proposalId="prop-1" />);

      expect(screen.queryByRole("button", { name: "approve" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "reject" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "withdraw" })).not.toBeInTheDocument();
    });

    it("disables the decision buttons while a mutation is in flight", () => {
      state.proposal = makeProposal({ status: "pending" });
      state.canApprove = true;
      state.mutationLoading = true;
      render(<SecretProposalDetailClient proposalId="prop-1" />);

      expect(screen.getByRole("button", { name: "approve" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "reject" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "withdraw" })).toBeDisabled();
    });
  });

  describe("diff summary fallback", () => {
    it("uses payloadDiff.summary as the header description when present", () => {
      state.proposal = makeProposal({
        payloadDiff: { summary: "Rotate DATABASE_URL", before: {}, after: {} },
      });
      render(<SecretProposalDetailClient proposalId="prop-1" />);

      expect(screen.getByTestId("page-description")).toHaveTextContent("Rotate DATABASE_URL");
    });

    it("falls back to `<op> on <env>` when payloadDiff carries no summary", () => {
      state.proposal = makeProposal({
        op: "set",
        environmentName: "prod",
        payloadDiff: { before: {}, after: { DATABASE_URL: "***" } },
      });
      render(<SecretProposalDetailClient proposalId="prop-1" />);

      expect(screen.getByTestId("page-description")).toHaveTextContent("set on prod");
    });
  });
});
