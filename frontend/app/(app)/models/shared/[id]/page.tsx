import { SharedModelClient } from "./shared-model-client";
export default async function SharedModelPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SharedModelClient id={id} />;
}
