import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { CONFIGURATION_GROUPS } from "./configuration-groups";

it("documents the unbuffered logging default supplied by the real control-plane image", () => {
  const dockerfile = readFileSync(resolve(process.cwd(), "../backend/Dockerfile"), "utf8");
  const actualDefault = dockerfile.match(/^ENV PYTHONUNBUFFERED=(\S+)$/m)?.[1];
  expect(actualDefault).toBeDefined();
  const variable = CONFIGURATION_GROUPS.flatMap((group) => group.vars).find(
    (row) => row.name === "PYTHONUNBUFFERED"
  );
  expect(variable).toMatchObject({ source: "image", default: actualDefault, required: false });
});
