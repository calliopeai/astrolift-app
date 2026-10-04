"use client";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { NativeModelConnectScreen } from "./NativeModelConnectScreen";
import { useNativeModelConnection } from "./use-native-model-connection";

export function NativeModelConnectClient() {
  const { org } = useActiveOrg(),
    { user } = useMe();
  return <Context key={`${user?.id}:${org?.id}`} />;
}
function Context() {
  return <NativeModelConnectScreen {...useNativeModelConnection()} />;
}
