"use client";
import { useQuery } from "@apollo/client/react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { GET_MODEL_HOSTING_ACTION } from "@/graphql/models/hosting.queries";
import { GET_BEDROCK_MODEL_SUPPORT } from "@/graphql/models/native-models.queries";
import type {
  GetModelHostingActionQuery,
  GetBedrockModelSupportQuery,
} from "@/graphql/__generated__/operations";
import { ModelAddChoicePanel } from "./ModelAddChoicePanel";

export function ModelAddChoiceClient() {
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const { user, loading: actorLoading, error: actorError } = useMe();
  const skip = orgLoading || actorLoading || !!orgError || !!actorError || !org?.id || !user?.id;
  const options = {
    skip,
    variables: { organizationId: org?.id ?? "" },
    fetchPolicy: "no-cache" as const,
    context: { queryDeduplication: false },
  };
  const hosting = useQuery<GetModelHostingActionQuery>(GET_MODEL_HOSTING_ACTION, options);
  const native = useQuery<GetBedrockModelSupportQuery>(GET_BEDROCK_MODEL_SUPPORT, options);
  const failure = orgError?.message ?? actorError?.message ?? null;
  return (
    <ModelAddChoicePanel
      retryDisabled={skip || hosting.loading || native.loading}
      onRetry={() => {
        if (!skip) {
          void hosting.refetch().catch(() => {});
          void native.refetch().catch(() => {});
        }
      }}
      hosting={{
        allowed:
          skip || hosting.loading
            ? null
            : !hosting.error && hosting.data?.modelHostingAction?.allowed === true,
        reason:
          failure ?? hosting.error?.message ?? hosting.data?.modelHostingAction?.reason ?? null,
      }}
      connection={{
        allowed:
          skip || native.loading
            ? null
            : !native.error &&
              native.data?.bedrockModelConnectionSupport?.enabled === true &&
              native.data.bedrockModelConnectionSupport.allowed === true,
        reason:
          failure ??
          native.error?.message ??
          native.data?.bedrockModelConnectionSupport?.reason ??
          null,
      }}
    />
  );
}
