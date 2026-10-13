"use client";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { SendingAccountView } from "@/components/screens/apps/managed-services/EmailDetailSheet";
import { useEmailDetail } from "@/components/screens/apps/managed-services/use-email-detail";
import { DomainEmailScreen } from "@/components/screens/email-delivery/DomainEmailScreen";
import { EmailDeliveryClient } from "@/components/screens/email-delivery/EmailDeliveryClient";
import { useDomainEmail } from "@/components/screens/email-delivery/use-domain-email";
function SendingAccount({ id }: { id: string }) {
  const { detail, loading } = useEmailDetail(id, true);
  return <SendingAccountView detail={detail} loading={loading} />;
}
function DomainContext({ id }: { id: string }) {
  const state = useDomainEmail(id);
  const permissions = useMyPermissions();
  return (
    <DomainEmailScreen
      {...state}
      installAlertMailHref={
        !permissions.loading && !permissions.error && permissions.can("org.update")
          ? "/settings/notifications?section=install-email"
          : undefined
      }
      delivery={
        state.service ? (
          <>
            <SendingAccount key={`account:${state.service.id}`} id={state.service.id} />
            <EmailDeliveryClient
              key={state.service.id}
              id={state.service.id}
              name={state.service.name ?? state.service.kind}
            />
          </>
        ) : null
      }
    />
  );
}
export function DomainEmailClient({ id }: { id: string }) {
  const { org } = useActiveOrg(),
    { user } = useMe();
  return <DomainContext key={`${user?.id ?? ""}:${org?.id ?? ""}:${id}`} id={id} />;
}
