import type { FormDefinition, FormSubmission } from "@/graphql/forms/forms.types";

import type { FormDetailScreenProps } from "./FormDetailScreen";
import type { FormsListScreenProps } from "./FormsListScreen";
import type { FormSubmitScreenProps } from "./FormSubmitScreen";
import type { NewFormScreenProps } from "./NewFormScreen";

/** Hand-typed fixtures for the forms screens. */

const yes = async () => true;

export const LONG =
  "Quarterly platform capacity and regional expansion request for shared production workloads with a deliberately long name";

export const SCHEMA: Record<string, unknown> = {
  type: "object",
  title: "Request a cluster",
  description: "Ask the platform team for a new cluster in your region.",
  required: ["team", "region"],
  properties: {
    team: { type: "string", title: "Team" },
    contact: { type: "string", title: "Contact email", format: "email" },
    region: { type: "string", title: "Region", enum: ["us-west-2", "eu-west-1"] },
    gpu: { type: "boolean", title: "Needs GPUs" },
    gpu_count: { type: "integer", title: "GPU count" },
    tags: { type: "array", title: "Tags", items: { type: "string" } },
    justification: {
      type: "string",
      title: "Justification",
      description: "Why this cluster, why now.",
      maxLength: 2000,
    },
    owner: {
      type: "object",
      title: "Owner",
      properties: {
        name: { type: "string", title: "Name" },
        cost_center: { type: "string", title: "Cost center" },
      },
    },
  },
};

export const FORM: FormDefinition = {
  id: "form-1",
  name: "Request a cluster",
  slug: "cluster-request",
  description: "Ask the platform team for a new cluster in your region.",
  status: "published",
  version: 3,
  schema: SCHEMA,
  isPublic: true,
  logicRules: [],
  fieldConfig: {},
  publishedAt: "2026-09-01T00:00:00Z",
  createdAt: "2026-08-20T00:00:00Z",
  updatedAt: "2026-09-01T00:00:00Z",
  submissionCount: 3,
};

export const DRAFT_FORM: FormDefinition = {
  ...FORM,
  id: "form-2",
  name: "Expense report",
  slug: "expense-report",
  description: "",
  status: "draft",
  version: 1,
  isPublic: false,
  publishedAt: null,
  submissionCount: 0,
  schema: {
    type: "object",
    properties: { amount: { type: "number", title: "Amount" } },
    required: ["amount"],
  },
};

export const ARCHIVED_FORM: FormDefinition = {
  ...FORM,
  id: "form-3",
  name: "2025 offsite RSVP",
  slug: "offsite-rsvp-2025",
  status: "archived",
  version: 2,
  isPublic: false,
  submissionCount: 41,
};

export const LONG_FORM: FormDefinition = {
  ...FORM,
  id: "form-4",
  name: LONG,
  slug: "quarterly-platform-capacity-and-regional-expansion-request-for-shared-production-workloads",
  description: `${LONG}, filed by every team that wants more than one region and a GPU node pool.`,
};

export const SUBMISSIONS: FormSubmission[] = [
  {
    id: "sub-1",
    payload: {
      team: "payments",
      region: "us-west-2",
      gpu: false,
      tags: ["pci", "prod"],
      spec: "https://example.com/uploads/payments-cluster-spec.pdf",
    },
    status: "submitted",
    submittedAt: "2026-09-27T14:02:00Z",
    createdAt: "2026-09-27T14:02:00Z",
    formName: "Request a cluster",
    formVersion: 3,
    formSlug: "cluster-request",
    submitterDisplayName: "Ana Ortiz",
    submitterEmail: "ana@example.com",
  },
  {
    id: "sub-2",
    payload: { team: "ml-platform", region: "eu-west-1", gpu: true, gpu_count: 8 },
    status: "approved",
    submittedAt: "2026-09-20T09:30:00Z",
    createdAt: "2026-09-20T09:30:00Z",
    formName: "Request a cluster",
    formVersion: 3,
    formSlug: "cluster-request",
    submitterDisplayName: "Sam Lee",
    submitterEmail: "sam@example.com",
  },
  {
    id: "sub-3",
    payload: {},
    status: "rejected",
    submittedAt: "2026-09-02T17:45:00Z",
    createdAt: "2026-09-02T17:45:00Z",
    formName: "Request a cluster",
    formVersion: 2,
    formSlug: "cluster-request",
    submitterDisplayName: "Kai Chen",
    submitterEmail: "kai@example.com",
  },
];

export const LONG_SUBMISSION: FormSubmission = {
  ...SUBMISSIONS[0],
  id: "sub-long",
  payload: {
    team: LONG,
    justification: `${LONG}.\nWe need a second region for failover and a GPU pool for the ranking models.`,
    owner: { name: "Ana Ortiz", cost_center: "CC-4411" },
    notes: null,
  },
};

/** Enough rows for the pager to show (page size is 25). */
export const MANY_SUBMISSIONS: FormSubmission[] = Array.from({ length: 30 }, (_, i) => ({
  ...SUBMISSIONS[i % SUBMISSIONS.length],
  id: `sub-many-${i}`,
  submittedAt: `2026-09-${String((i % 27) + 1).padStart(2, "0")}T12:00:00Z`,
}));

export const LIST: FormsListScreenProps = {
  forms: [FORM, DRAFT_FORM, ARCHIVED_FORM],
  loading: false,
  error: null,
};

export const DETAIL: FormDetailScreenProps = {
  slug: FORM.slug,
  form: FORM,
  loading: false,
  error: null,
  submissions: SUBMISSIONS,
  submissionsLoading: false,
  onPublish: yes,
  onArchive: yes,
};

export const NEW_FORM: NewFormScreenProps = { onCreate: yes };

export const SUBMIT: FormSubmitScreenProps = {
  slug: FORM.slug,
  formDef: FORM,
  loading: false,
  error: null,
  onSubmit: async () => ({ ok: true, submissionId: "sub-4", errors: [] }),
};
