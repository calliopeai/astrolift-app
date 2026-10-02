import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { buildSchema, execute, validate } from "graphql";
import { describe, expect, it } from "vitest";

import { RESTART_WORKLOAD, SCALE_WORKLOAD } from "@/graphql/lifecycle/lifecycle.mutations";
import {
  GET_WORKLOAD,
  LIST_WORKLOADS,
  LIST_WORKLOADS_PAGE,
} from "@/graphql/registry/registry.queries";

const schema = buildSchema(readFileSync(path.resolve(process.cwd(), "schema.graphql"), "utf8"));

describe("workload client operation contracts", () => {
  it.each([GET_WORKLOAD, LIST_WORKLOADS, LIST_WORKLOADS_PAGE, RESTART_WORKLOAD, SCALE_WORKLOAD])(
    "validates the actual client document against the exported SDL",
    (document) => {
      expect(validate(schema, document)).toEqual([]);
    }
  );
  it.each([
    [RESTART_WORKLOAD, "restartAstroliftWorkload"],
    [SCALE_WORKLOAD, "scaleAstroliftWorkload"],
  ] as const)(
    "binds a workload version to the actual mutation argument",
    async (document, field) => {
      const calls: unknown[] = [];
      const input =
        field === "scaleAstroliftWorkload"
          ? { workloadId: "workload-guid", replicas: 4 }
          : { workloadId: "workload-guid" };
      const result = await execute({
        schema,
        document,
        variableValues: { input, ifMatchVersion: 7 },
        rootValue: {
          [field]: (args: unknown) => {
            calls.push(args);
            return { ok: true, errors: [], data: null };
          },
        },
      });
      expect(result.errors).toBeUndefined();
      expect(calls).toEqual([
        {
          input: {
            ...input,
            environmentId: null,
            expectedClusterId: null,
            expectedNamespace: null,
            ifMatchAppVersion: null,
            ifMatchClusterVersion: null,
            ifMatchEnvironmentVersion: null,
          },
          ifMatchVersion: 7,
        },
      ]);
    }
  );
  it.each([
    [RESTART_WORKLOAD, "restartAstroliftWorkload"],
    [SCALE_WORKLOAD, "scaleAstroliftWorkload"],
  ] as const)("binds every reviewed environment precondition to %s", async (document, field) => {
    const calls: unknown[] = [];
    const input = {
      workloadId: "workload-guid",
      ...(field === "scaleAstroliftWorkload" ? { replicas: 4 } : {}),
      environmentId: "environment-guid",
      expectedClusterId: "cluster-guid",
      expectedNamespace: "reviewed-namespace",
      ifMatchAppVersion: 11,
      ifMatchClusterVersion: 13,
      ifMatchEnvironmentVersion: 17,
    };
    const result = await execute({
      schema,
      document,
      variableValues: { input, ifMatchVersion: 7 },
      rootValue: {
        [field]: (args: unknown) => {
          calls.push(args);
          return { ok: true, errors: [], data: null };
        },
      },
    });
    expect(result.errors).toBeUndefined();
    expect(calls).toEqual([{ input, ifMatchVersion: 7 }]);
  });
});

const messages = path.resolve(process.cwd(), "messages");
it.each(readdirSync(messages).filter((file) => file.endsWith(".json")))(
  "keeps modified workload action copy complete in %s",
  (file) => {
    const read = (name: string) =>
      JSON.parse(readFileSync(path.join(messages, name), "utf8")).apps.workloadActions as Record<
        string,
        string
      >;
    const actual = read(file);
    expect(Object.keys(actual).sort()).toEqual(Object.keys(read("en.json")).sort());
    expect(
      Object.values(actual).every((value) => typeof value === "string" && value.trim().length > 0)
    ).toBe(true);
  }
);
