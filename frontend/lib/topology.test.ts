import { describe, expect, it } from "vitest";

import { classifyTopology, type TopologyInput } from "./topology";

const w = (kind: TopologyInput["workloads"][number]["kind"], name: string) => ({ kind, name });

describe("classifyTopology", () => {
  it.each<[string, TopologyInput, string]>([
    ["one deployment", { workloads: [w("deployment", "web")] }, "service"],
    [
      "deployment + managed postgres",
      { workloads: [w("deployment", "web")], managedServices: ["postgres"] },
      "service-data",
    ],
    [
      "deployment + self-hosted db",
      { workloads: [w("deployment", "api"), w("statefulset", "postgres")] },
      "service-data",
    ],
    [
      "email is not data",
      { workloads: [w("deployment", "web")], managedServices: ["email"] },
      "service",
    ],
    [
      "web + named worker",
      { workloads: [w("deployment", "web"), w("deployment", "worker")] },
      "service-worker",
    ],
    [
      "two services sharing a queue",
      { workloads: [w("deployment", "web"), w("deployment", "mailer")], managedServices: ["sqs"] },
      "service-worker",
    ],
    [
      "three services",
      {
        workloads: [w("deployment", "web"), w("deployment", "billing"), w("deployment", "search")],
      },
      "microservices",
    ],
    [
      "two services, no queue",
      { workloads: [w("deployment", "web"), w("deployment", "api")] },
      "microservices",
    ],
    [
      "service + agent",
      { workloads: [w("deployment", "web"), w("agent", "triage")] },
      "service-agent",
    ],
    ["agents only", { workloads: [w("agent", "a"), w("agent", "b")] }, "agent"],
    ["agent + cron", { workloads: [w("agent", "a"), w("cronjob", "sweep")] }, "mixed"],
    [
      "functions",
      { workloads: [w("function", "resize"), w("function", "thumb")], managedServices: ["s3"] },
      "functions",
    ],
    ["cron", { workloads: [w("cronjob", "nightly")] }, "scheduled"],
    ["job and task", { workloads: [w("job", "migrate"), w("task", "seed")] }, "task"],
    ["workflow", { workloads: [w("workflow", "release")] }, "workflow"],
    ["service + function", { workloads: [w("deployment", "web"), w("function", "hook")] }, "mixed"],
    ["nothing yet", { workloads: [] }, "service"],
  ])("%s", (_name, input, expected) => {
    expect(classifyTopology(input)).toBe(expected);
  });
});
