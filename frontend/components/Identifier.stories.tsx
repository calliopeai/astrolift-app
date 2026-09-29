import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Identifier } from "@/components/Identifier";

/** SHAs, digests, ARNs, ids and URLs, one way everywhere (spec 44 §7). */
const meta: Meta<typeof Identifier> = { title: "Primitives/Identifier", component: Identifier };
export default meta;

const SHA = "1112015d8a9b7c6e5f4d3c2b1a0f9e8d7c6b5a49";
const DIGEST = `sha256:${"4f1c9e0a".repeat(8)}`;
const ARN = "arn:aws:acm:us-west-2:123456789012:certificate/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b";

export const Short: StoryObj<typeof Identifier> = {
  render: () => (
    <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-2 text-sm">
      <dt className="text-muted-foreground">SHA</dt>
      <dd>
        <Identifier value={SHA} kind="sha" />
      </dd>
      <dt className="text-muted-foreground">Digest</dt>
      <dd>
        <Identifier value={DIGEST} kind="digest" />
      </dd>
      <dt className="text-muted-foreground">ARN</dt>
      <dd>
        <Identifier value={ARN} kind="arn" />
      </dd>
      <dt className="text-muted-foreground">URL</dt>
      <dd>
        <Identifier value="https://checkout.astro.example.com/healthz" kind="url" />
      </dd>
    </dl>
  ),
};

/** The body form in a narrow frame: every character, wrapping, never overflowing. */
export const FullInNarrowFrame: StoryObj<typeof Identifier> = {
  render: () => (
    <div className="border-border w-64 rounded-sm border p-3 text-sm">
      <Identifier value={ARN} kind="arn" form="full" />
    </div>
  ),
};
