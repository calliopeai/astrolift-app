import { PolicyClient } from "./policy-client";

export const metadata = { title: "Policy · Administration · Astrolift" };

export default async function PolicyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PolicyClient id={id} />;
}
