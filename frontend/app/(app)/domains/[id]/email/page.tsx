import { DomainEmailClient } from "./domain-email-client";
export const metadata = { title: "Email delivery · Astrolift" };
export default async function DomainEmailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DomainEmailClient id={id} />;
}
