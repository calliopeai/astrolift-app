import { DnsConnectionClient } from "./dns-connection-client";

export default async function ConnectDnsPage({
  searchParams,
}: {
  searchParams: Promise<{ domainId?: string }>;
}) {
  const { domainId } = await searchParams;
  return <DnsConnectionClient domainId={domainId} />;
}
