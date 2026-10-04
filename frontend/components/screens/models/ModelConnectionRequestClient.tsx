"use client";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { ModelConnectionRequestScreen } from "./ModelConnectionRequestScreen";
import { useModelConnectionRequest } from "./use-model-connection-request";
export function ModelConnectionRequestClient(props: {
  id: string;
  version: number;
  review: boolean;
}) {
  const { org } = useActiveOrg(),
    { user } = useMe();
  return (
    <Context
      key={`${user?.id}:${org?.id}:${props.id}:${props.version}:${props.review}`}
      {...props}
    />
  );
}
function Context({ id, version, review }: { id: string; version: number; review: boolean }) {
  const props = useModelConnectionRequest(id, version, review);
  return <ModelConnectionRequestScreen {...props} />;
}
