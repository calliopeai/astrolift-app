import { ModelsClient } from "./models-client";

export const metadata = { title: "Models · Astrolift" };

/**
 * Models: every model endpoint the org runs (#2040), hosted on its own GPUs
 * (vLLM, KServe) or served by a cloud (Bedrock, Azure OpenAI, Foundry,
 * Vertex). Apps and agents bind to any of them through the same
 * MODEL_* envelope.
 */
export default function ModelsPage() {
  return <ModelsClient />;
}
