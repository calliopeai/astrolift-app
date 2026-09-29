import type { DriversScreenProps } from "./DriversScreen";
import type { HelpScreenProps } from "./HelpScreen";
import type { Tutorial } from "./tutorial-types";
import type { DriverRow } from "./use-drivers";
import type { EventSpec } from "./webhook-events-data";

/** Hand-typed fixtures for the docs-e group (webhook events, drivers, help, tutorials). */

const LONG =
  "a-deliberately-long-unbroken-identifier-that-keeps-going-past-the-column-width-to-test-wrapping";

// ----- tutorials -----

export const DOCS_E_TUTORIAL: Tutorial = {
  slug: "deploy-first-app",
  title: "Deploy your first app",
  description: "Register an app, point it at a cluster, and ship a first deployment.",
  level: "Beginner",
  duration: "10 min",
  steps: [
    {
      title: "Register the app",
      body: "Create a RegisteredApp from the Apps page and pick the source repository.",
    },
    {
      title: "Deploy",
      body: "Push to the default branch; the deployment Workflow picks it up.",
      code: "git push origin main",
    },
  ],
};

export const DOCS_E_TUTORIALS: Tutorial[] = [
  DOCS_E_TUTORIAL,
  {
    slug: "managed-postgres",
    title: "Bind a managed Postgres",
    description: "Declare a managed service on the manifest and read its credentials at runtime.",
    level: "Intermediate",
    duration: "20 min",
    steps: [
      { title: "Declare it", body: "Add a [[managed_services]] block.", code: 'kind = "postgres"' },
    ],
  },
  {
    slug: "custom-driver",
    title: "Write a custom provider driver",
    description: "Implement the driver protocol for a new cloud and register it as a plugin.",
    level: "Advanced",
    duration: "45 min",
    steps: [{ title: "Implement the protocol", body: "Subclass the driver base class." }],
  },
];

export const DOCS_E_TUTORIAL_LONG: Tutorial = {
  slug: LONG,
  title: `A tutorial whose title runs long ${LONG}`,
  description: `A description that keeps going ${LONG} to check that the card and header wrap.`,
  level: "Intermediate-to-advanced-with-a-long-level",
  duration: "2 hours 45 min",
  steps: [
    {
      title: `A step title ${LONG}`,
      body: `A step body ${LONG} that should wrap inside the article width.`,
      code: `./run.sh manage ${LONG} --with-a-very-long-flag=${LONG}`,
    },
  ],
};

// ----- webhook events -----

export const DOCS_E_EVENTS: EventSpec[] = [
  {
    name: "deployment.started",
    category: "Deployments",
    when: "Fires when a deployment Workflow transitions out of pending and begins applying.",
    payloadFields: [
      { name: "deployment_id", type: "string", description: "ULID of the Deployment row." },
      { name: "app_slug", type: "string", description: "Slug of the RegisteredApp." },
    ],
    example: `{
  "deployment_id": "dep_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api"
}`,
  },
  {
    name: "alert.fired",
    category: "Alerts",
    when: "Fires when an alert rule crosses its threshold.",
    payloadFields: [{ name: "rule_id", type: "string", description: "ULID of the alert rule." }],
    example: `{
  "rule_id": "alr_01J7K2A6P3RGBYZW8XV4HMQDFC"
}`,
  },
];

export const DOCS_E_EVENTS_LONG: EventSpec[] = [
  {
    name: `deployment.${LONG}`,
    category: "Deployments with a long category name",
    when: `Fires when something happens ${LONG} and keeps describing it.`,
    payloadFields: [
      { name: LONG, type: "map<string, list<string>>", description: `Described ${LONG}.` },
    ],
    example: `{
  "${LONG}": "${LONG}"
}`,
  },
];

// ----- help -----

export const DOCS_E_HELP: HelpScreenProps = {
  platformVersion: "0.1.38",
  org: { name: "CONFLICT", slug: "conflict" },
  copied: false,
  copyDiagnostics: () => {},
};

export const DOCS_E_HELP_LONG: HelpScreenProps = {
  ...DOCS_E_HELP,
  platformVersion: `0.1.38+${LONG}`,
  org: { name: `An organization with a long name ${LONG}`, slug: LONG },
};

// ----- drivers -----

const allCaps = (on: boolean): Record<string, boolean> => ({
  cluster: on,
  ingress: on,
  dns: on,
  tls: on,
  secrets: on,
  identity: on,
  registry: on,
});

export const DOCS_E_DRIVER_ROWS: DriverRow[] = [
  {
    slug: "aws",
    name: "AWS",
    version: "1.4.0",
    isEnabled: true,
    capabilities: allCaps(true),
    managedServices: ["postgres", "redis", "object_storage"],
  },
  {
    slug: "k8s_native",
    name: "Kubernetes-native",
    version: "0.9.2",
    isEnabled: true,
    capabilities: { ...allCaps(false), cluster: true, ingress: true, tls: true, secrets: true },
    managedServices: [],
  },
];

export const DOCS_E_DRIVERS: DriversScreenProps = {
  loading: false,
  rows: DOCS_E_DRIVER_ROWS,
  clusterCountByProvider: new Map([["aws", 3]]),
  managedKindColumns: ["object_storage", "postgres", "redis"],
  usingFallback: false,
};

export const DOCS_E_DRIVERS_LOADING: DriversScreenProps = {
  ...DOCS_E_DRIVERS,
  loading: true,
  rows: [],
  clusterCountByProvider: new Map(),
};

/** The hook's no-plugins branch: the static provider list with the fallback notice. */
export const DOCS_E_DRIVERS_FALLBACK: DriversScreenProps = {
  loading: false,
  rows: [
    {
      slug: "aws",
      name: "AWS",
      capabilities: allCaps(true),
      managedServices: ["postgres", "redis", "object_storage", "kafka", "search"],
    },
    {
      slug: "k8s_native",
      name: "Kubernetes-native",
      capabilities: { ...allCaps(false), cluster: true, ingress: true, tls: true, secrets: true },
      managedServices: [],
    },
  ],
  clusterCountByProvider: new Map(),
  managedKindColumns: ["kafka", "object_storage", "postgres", "redis", "search"],
  usingFallback: true,
};

export const DOCS_E_DRIVERS_LONG: DriversScreenProps = {
  loading: false,
  rows: [
    {
      slug: LONG,
      name: `A provider plugin with a long name ${LONG}`,
      version: `1.0.0-${LONG}`,
      isEnabled: true,
      capabilities: allCaps(true),
      managedServices: [`vector_index_${LONG}`],
    },
  ],
  clusterCountByProvider: new Map([[LONG, 1]]),
  managedKindColumns: [`vector_index_${LONG}`],
  usingFallback: false,
};
