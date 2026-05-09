import { ManifestPreviewClient } from "./manifest-preview-client";

export const metadata = { title: "Manifest preview · Astrolift" };

export default async function ManifestPreviewPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  // No PreloadQuery — the client component fetches both queries
  // directly. This page already lives inside the (app) layout's two
  // PreloadQuery wrappers (GET_ME + GET_MY_PERMISSIONS); adding more
  // streams routinely hangs the dev-mode Suspense boundary in
  // Next.js 16. Client-side fetch is fast enough on this surface.
  const { slug } = await params;
  return <ManifestPreviewClient slug={slug} />;
}
