import type { ReactNode } from "react";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProjectResourcesClient } from "./project-resources-client";

const project = {
  id: "project-1",
  slug: "emr-bug-triage",
  name: "EMR Bug Triage",
  organization: { id: "org-1", slug: "steadymd", name: "SteadyMD" },
  team: { id: "team-1", slug: "engineering", name: "Engineering" },
};

const catalog = [
  {
    id: "aws:postgres:rds",
    providerPluginSlug: "aws",
    kind: "postgres",
    variant: "rds",
    displayName: "Amazon RDS for PostgreSQL",
    description: "Amazon RDS for PostgreSQL",
    status: "ga",
    available: true,
    unavailableReason: "",
    isDefaultForKind: true,
    sizeOptions: ["small", "medium", "large", "xlarge", "custom"],
    configSchema: { type: "object", properties: {} },
    bindingEnvs: ["DATABASE_URL"],
    issueUrl: "",
  },
  {
    id: "aws:postgres:aurora_postgres_serverless_v2",
    providerPluginSlug: "aws",
    kind: "postgres",
    variant: "aurora_postgres_serverless_v2",
    displayName: "Amazon Aurora PostgreSQL Serverless v2 cluster",
    description: "Amazon Aurora PostgreSQL Serverless v2 cluster",
    status: "planned",
    available: false,
    unavailableReason: "Driver is a non-provisioning stub or planned capability.",
    isDefaultForKind: false,
    sizeOptions: ["small", "medium", "large", "xlarge", "custom"],
    configSchema: { type: "object", properties: {} },
    bindingEnvs: [],
    issueUrl: "https://github.com/calliopeai/astrolift-app/issues/1283",
  },
];

vi.mock("@apollo/client/react", () => ({
  useLazyQuery: () => [vi.fn(), { loading: false }],
  useMutation: () => [vi.fn(), { loading: false }],
  useQuery: (document: { definitions?: Array<{ kind: string; name?: { value: string } }> }) => {
    const operation =
      document.definitions?.find((definition) => definition.kind === "OperationDefinition")?.name
        ?.value ?? "";
    const data: Record<string, unknown> = {
      ListProjects: { astroliftProjects: [project] },
      ListProjectResources: {
        astroliftProjectResourceClusters: [
          {
            id: "cluster-1",
            slug: "production",
            name: "Production",
            providerPluginSlug: "aws",
            region: "us-east-1",
            isActive: true,
            lifecycle: "managed",
          },
        ],
        astroliftProjectManagedServices: [
          {
            id: "filesystem-1",
            name: "agent-workspace",
            kind: "filesystem",
            variant: "rook_cephfs",
            status: "active",
            statusError: "",
            config: {},
            projectSlug: "emr-bug-triage",
            ownerScope: "project",
            clusterSlug: "production",
            environmentName: "shared",
            createdAt: "2026-08-14T00:00:00Z",
            updatedAt: "2026-08-14T00:00:00Z",
            lastActionAt: null,
            lastActionKind: "",
            editableFields: [],
            attachments: [],
            volumeBindings: [
              {
                id: "binding-1",
                name: "agent-workspace",
                mountPath: "/workspace",
                subPath: "",
                sourceKind: "dynamic_pvc",
                protocol: "cephfs",
                claimName: "",
                claimNamespace: "",
                storageClassName: "rook-cephfs",
                csiDriver: "rook-ceph.cephfs.csi.ceph.com",
                readOnly: false,
                capacity: "100Gi",
                accessModes: ["ReadWriteMany"],
                workloadNames: [],
                containerNames: [],
                credentialReferenceCount: 0,
              },
            ],
          },
        ],
        astroliftProjectSecretBundles: [],
      },
      ListProjectManagedServiceCatalog: {
        astroliftProjectManagedServiceCatalog: catalog,
      },
      ListAgentWorkloads: { agentWorkloads: [] },
      ListApps: { astroliftApps: [] },
      ListEnvironments: { astroliftEnvironments: [] },
    };
    return { data: data[operation], loading: false, error: undefined, refetch: vi.fn() };
  },
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

// The page picks its section from `?section=`; the default (infrastructure) here.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/projects/storefront/resources",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true }),
}));

vi.mock("@/hooks/use-confirm", () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}));

vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: ReactNode }) => <>{children}</>,
  DialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogFooter: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}));

describe("ProjectResourcesClient provider catalogue", () => {
  it("keeps planned variants inspectable but not provisionable", () => {
    render(<ProjectResourcesClient slug="emr-bug-triage" />);

    const picker = screen.getByLabelText("Provider resource");
    const submit = screen.getByRole("button", { name: "Provision" });
    expect(picker).toHaveValue("aws:postgres:rds");
    expect(submit).toBeEnabled();

    fireEvent.change(picker, {
      target: { value: "aws:postgres:aurora_postgres_serverless_v2" },
    });

    expect(screen.getByText(/non-provisioning stub or planned capability/i)).toBeVisible();
    expect(screen.getByRole("link", { name: "View implementation issue" })).toHaveAttribute(
      "href",
      "https://github.com/calliopeai/astrolift-app/issues/1283"
    );
    expect(submit).toBeDisabled();
  });

  it("shows the StorageClass behind a dynamic filesystem mount", () => {
    render(<ProjectResourcesClient slug="emr-bug-triage" />);

    expect(screen.getByText("StorageClass rook-cephfs")).toBeVisible();
    expect(screen.getByText("/workspace")).toBeVisible();
  });
});
