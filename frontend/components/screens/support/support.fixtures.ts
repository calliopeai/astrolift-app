import type { SupportScreenProps } from "./SupportScreen";
import { EMPTY_DRAFT, type SupportTicket } from "./use-support";

const noop = () => {};

export const TICKET: SupportTicket = {
  id: "5f0c2d4e-8a7b-4c1d-9e3f-2a1b0c9d8e7f",
  title: "Deploys stuck in pending since this morning",
  body: "Every deploy to production waits in pending. Staging deploys fine.",
  status: "triaged",
  severity: 2,
  created_at: "2026-10-13T09:12:00Z",
  updated_at: "2026-10-13T10:40:00Z",
  coverage_summary: {
    reference: "SUP-2026-014",
    package: "standard",
    window: "Mon–Fri 08:00–18:00 UTC−07:00",
    continuous_for_severity: true,
  },
  events: [
    {
      id: "e1",
      action: "created",
      author: "operator@acme.example",
      at: "2026-10-13T09:12:00Z",
      detail: null,
      from: null,
      to: null,
    },
    {
      id: "e2",
      action: "status_changed",
      author: null,
      at: "2026-10-13T09:30:00Z",
      detail: null,
      from: "new",
      to: "triaged",
    },
    {
      id: "e3",
      action: "support_replied",
      author: "Calliope AI support",
      at: "2026-10-13T10:40:00Z",
      detail:
        "We see the cluster's agent heartbeat stopped at 08:57. Can you share `astro cluster status`?",
      from: null,
      to: null,
    },
  ],
};

export const TICKETS: SupportTicket[] = [
  TICKET,
  {
    ...TICKET,
    id: "6a1d3e5f-9b8c-4d2e-8f4a-3b2c1d0e9f8a",
    title: "How do we rotate the edge client secret?",
    status: "released",
    severity: 4,
    updated_at: "2026-10-09T15:00:00Z",
    events: [],
  },
];

export const SUPPORT: SupportScreenProps = {
  tickets: TICKETS,
  loading: false,
  error: null,
  unavailable: false,
  onRetry: noop,
  selected: null,
  detailLoading: false,
  detailError: null,
  onOpen: noop,
  draft: EMPTY_DRAFT,
  onDraft: noop,
  creating: false,
  createError: null,
  createdId: null,
  onCreate: noop,
  reply: "",
  onReply: noop,
  replying: false,
  replyError: null,
  onSendReply: noop,
};
