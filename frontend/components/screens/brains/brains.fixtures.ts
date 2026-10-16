import type { BrainConnection } from "./brain-connection";
import type { BrainRow } from "./use-brains";

export const BRAINS: BrainRow[] = [
  {
    id: "b0000000-0000-4000-8000-000000000001",
    slug: "leo-brain",
    name: "Leo's brain",
    kind: "mimwell",
    team: "platform",
    project: "core",
    environments: [{ name: "production", url: "https://brain.acme.example" }],
    sharedWith: [
      { team: "medops", access: "viewer" },
      { team: "sales", access: "viewer" },
    ],
  },
  {
    id: "b0000000-0000-4000-8000-000000000002",
    slug: "sales-brain",
    name: "Sales brain",
    kind: "mimwell",
    team: "sales",
    project: "gtm",
    environments: [],
    sharedWith: [],
  },
];

export const LONG_BRAIN: BrainRow = {
  id: "b0000000-0000-4000-8000-000000000003",
  slug: "a-very-long-semantic-layer-slug-that-keeps-going-for-the-layout-check",
  name: "A semantic layer with a name long enough to wrap in every locale the console ships",
  kind: "mimwell",
  team: "platform-engineering-and-reliability",
  project: "knowledge-and-ontology-services",
  environments: [
    {
      name: "production",
      url: "https://a-very-long-hostname.semantic-layers.acme-corporation.example/realm",
    },
    {
      name: "staging",
      url: "https://staging.a-very-long-hostname.semantic-layers.acme-corporation.example",
    },
  ],
  sharedWith: Array.from({ length: 8 }, (_, i) => ({
    team: `team-with-a-long-name-${i}`,
    access: "viewer",
  })),
};

export const CONNECTION: BrainConnection = {
  id: "b0000000-0000-4000-8000-000000000001",
  slug: "leo-brain",
  endpoints: ["https://brain.acme.example"],
  header: "X-Astrolift-Brain-Token",
  token: "eyJhbGciOiJFUzI1NiIsImtpZCI6ImZpeHR1cmUifQ.eyJzY29wZSI6ImJyYWluOnJlYWQifQ.c2ln",
  expiresAt: "2026-10-14T12:15:00+00:00",
};

export const HOST_PROJECTS = [
  { id: "p0000000-0000-4000-8000-000000000001", label: "platform/core" },
  { id: "p0000000-0000-4000-8000-000000000002", label: "medops/intake" },
];
