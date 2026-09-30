import { describe, expect, it } from "vitest";
import { redirectOf } from "./route-source.mjs";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

describe("route dictionary alias classification", () => {
  it("classifies the real former-agent route factory as a runtime alias", () => {
    const file = resolve("app/(app)/agents/[agentSlug]/secure/page.tsx");
    expect(redirectOf(file, readFileSync(file, "utf8"))).toEqual({ target: null });
  });
  it("retains computed server aliases for runtime redirect checks", () => {
    expect(
      redirectOf(
        "page.tsx",
        "export default async function Page() { redirect(legacyPeopleHref(await searchParams)); }"
      )
    ).toEqual({ target: null });
  });
  it("keeps a page that only redirects former query tabs", () => {
    expect(
      redirectOf(
        "page.tsx",
        "export default function Page() { if (target) redirect(target); return <AgentsClient />; }"
      )
    ).toBeNull();
  });
  it("does not treat quoted documentation as executable navigation", () => {
    expect(
      redirectOf(
        "page.tsx",
        'const example = "redirect(\\\"/apps\\\")"; export default () => <Docs />;'
      )
    ).toBeNull();
  });
  it("records external documentation aliases without following the remote site", () => {
    expect(
      redirectOf(
        "page.tsx",
        'export default function Page() { redirect("https://astrolift.dev/reference/"); }'
      )
    ).toEqual({ target: "https://astrolift.dev/reference/" });
  });
});
