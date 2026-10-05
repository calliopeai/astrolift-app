"use client";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { EmailDeliveryPanel } from "./EmailDeliveryPanel";
import { useEmailDelivery } from "./use-email-delivery";
function CurrentDelivery({ id, name }: { id: string; name: string }) {
  return <EmailDeliveryPanel {...useEmailDelivery(id, name)} />;
}
export function EmailDeliveryClient({ id, name }: { id: string; name: string }) {
  const { org } = useActiveOrg(),
    { user } = useMe();
  return <CurrentDelivery key={`${user?.id ?? ""}:${org?.id ?? ""}:${id}`} id={id} name={name} />;
}
