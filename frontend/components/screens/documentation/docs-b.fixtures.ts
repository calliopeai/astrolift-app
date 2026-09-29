import type { Group } from "./configuration-groups";

/** A small but representative set: every source badge and both required states. */
export const DOCS_B_GROUPS_FULL: Group[] = [
  {
    id: "core-platform",
    title: "Core platform",
    intro:
      "Process-level Django settings. Every install must set the first three; the rest have safe defaults.",
    vars: [
      {
        name: "DJANGO_SECRET_KEY",
        purpose: "Cryptographic key for signing cookies, CSRF tokens, password reset URLs.",
        default: "not-a-secret (dev only)",
        required: true,
        source: "operator",
      },
      {
        name: "DJANGO_CONFIGURATION",
        purpose: "Environment selector: Dev, Local, LocalPG, LocalVerbose, Tests, int, stg, Prd.",
        default: "Dev",
        required: false,
        source: "operator",
      },
      {
        name: "PYTHONUNBUFFERED",
        purpose: "Baked into the container image so logs flush immediately.",
        default: "1",
        required: false,
        source: "image",
      },
    ],
  },
  {
    id: "cache-queue",
    title: "Cache & queue",
    intro: "Redis powers Django's cache layer.",
    vars: [
      {
        name: "DJANGO_CACHE_URL",
        purpose: "Redis URL for the Django cache backend. Format: redis://host:port/db.",
        required: true,
        source: "operator",
      },
      {
        name: "REDIS_URL",
        purpose:
          "Injected into the pod env when an app binds the in-cluster Redis managed service.",
        required: false,
        source: "platform",
      },
    ],
  },
];

/** No groups: the page renders its conventions, the empty "On this page" nav, and the footer. */
export const DOCS_B_GROUPS_EMPTY: Group[] = [];

/** A group with no variables: the table renders its header only. */
export const DOCS_B_GROUPS_NO_VARS: Group[] = [
  { id: "feature-flags", title: "Feature flags", intro: "No flags are defined yet.", vars: [] },
];

export const DOCS_B_GROUPS_LONG: Group[] = [
  {
    id: "a-very-long-group-identifier-used-as-an-anchor-for-the-on-this-page-navigation",
    title:
      "An exceptionally long group title that keeps going to check wrapping in both the heading and the on-this-page navigation",
    intro: "A long introduction paragraph. ".repeat(12).trim(),
    vars: [
      {
        name: "ASTROLIFT_SOME_EXTREMELY_LONG_ENVIRONMENT_VARIABLE_NAME_WITHOUT_ANY_BREAKS_AT_ALL",
        purpose: "A purpose description that runs on for several sentences. ".repeat(8).trim(),
        default:
          "https://a-very-long-default-value.example.internal.cluster.local:8443/with/a/deep/path?and=query",
        required: true,
        source: "platform",
      },
    ],
  },
];
