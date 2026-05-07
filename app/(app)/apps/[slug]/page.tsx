import { AppDetailClient } from "./app-detail-client";

export const metadata = { title: "App · Astrolift" };

export default async function AppDetailPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <AppDetailClient slug={slug} />;
}
