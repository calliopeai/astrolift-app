import { notFound } from "next/navigation";

import { TutorialScreen } from "@/components/screens/documentation/TutorialScreen";

import { tutorials } from "../data";

export function generateStaticParams() {
  return tutorials.map((t) => ({ slug: t.slug }));
}

export default async function TutorialPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const tutorial = tutorials.find((t) => t.slug === slug);

  if (!tutorial) notFound();

  return <TutorialScreen tutorial={tutorial} />;
}
