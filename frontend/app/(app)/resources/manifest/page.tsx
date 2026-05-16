import { promises as fs } from "node:fs";
import path from "node:path";

import { ManifestClient } from "./manifest-client";
import type { ManifestSchema } from "./types";

export const metadata = {
  title: "Manifest reference · Resources · Astrolift",
};

// Server-render the schema from the checked-in JSON so the page works
// without a network call. When the backend grows a
// `manifestSchema` query (generated from the `astrolift_manifest`
// dataclasses), swap this for a GraphQL fetch.
async function loadSchema(): Promise<ManifestSchema> {
  const file = path.join(process.cwd(), "public", "manifest-schema.json");
  const raw = await fs.readFile(file, "utf-8");
  return JSON.parse(raw) as ManifestSchema;
}

export default async function ManifestReferencePage() {
  const schema = await loadSchema();
  return <ManifestClient schema={schema} />;
}
