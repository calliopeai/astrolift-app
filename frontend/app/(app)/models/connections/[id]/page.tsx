import { ModelConnectionRequestClient } from "@/components/screens/models/ModelConnectionRequestClient";
export default async function ModelConnectionRequestPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ version?: string; review?: string }>;
}) {
  const { id } = await params,
    query = await searchParams;
  return (
    <ModelConnectionRequestClient
      id={id}
      version={Number(query.version ?? 0)}
      review={query.review === "1"}
    />
  );
}
