"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type {
  AstroliftClusterAuthUser,
  AstroliftClusterAuthUsers,
} from "@/graphql/__generated__/schema";
import {
  CLUSTER_AUTH_USERS,
  CREATE_CLUSTER_AUTH_GROUP,
  CREATE_CLUSTER_AUTH_USER,
  DELETE_CLUSTER_AUTH_USER,
  RESET_CLUSTER_AUTH_USER_PASSWORD,
  SET_CLUSTER_AUTH_USER_ENABLED,
  SET_CLUSTER_AUTH_USER_GROUPS,
  SET_CLUSTER_AUTH_USER_PASSWORD,
} from "@/graphql/clusters/clusters.queries";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
import type { NewAuthUser } from "./types";

type Result = { ok: boolean; errors?: { message: string }[] | null };

export function useAuthUsers(clusterId: string) {
  const t = useTranslations("clusterSettings.authUsers");
  const {
    data,
    error: readError,
    loading,
    refetch,
  } = useQuery<{ astroliftClusterAuthUsers: AstroliftClusterAuthUsers | null }>(
    CLUSTER_AUTH_USERS,
    { variables: { clusterId }, fetchPolicy: "cache-and-network" }
  );
  const view = data?.astroliftClusterAuthUsers ?? null;
  // A group creation changes available groups before the subsequent membership write.
  const sourceKey = JSON.stringify([
    clusterId,
    view?.supported,
    view?.provider,
    view?.reason,
    view?.reachNote,
    view?.source,
    view?.users,
  ]);
  const expectedSource =
    view?.source &&
    view.source.providerPluginId &&
    view.source.providerPoolId &&
    view.source.sourceVersion
      ? {
          providerPluginId: view.source.providerPluginId,
          providerPoolId: view.source.providerPoolId,
          sourceVersion: view.source.sourceVersion,
        }
      : null;
  const lease = React.useMemo(() => ({ sourceKey }), [sourceKey]);
  const current = React.useRef<typeof lease | null>(lease);
  React.useLayoutEffect(() => {
    current.current = lease;
    return () => {
      current.current = null;
    };
  }, [lease]);
  const [createUser] = useMutation<{ createClusterAuthUser: Result }>(CREATE_CLUSTER_AUTH_USER);
  const [setPassword] = useMutation<{ setClusterAuthUserPassword: Result }>(
    SET_CLUSTER_AUTH_USER_PASSWORD
  );
  const [resetPassword] = useMutation<{ resetClusterAuthUserPassword: Result }>(
    RESET_CLUSTER_AUTH_USER_PASSWORD
  );
  const [setEnabled] = useMutation<{ setClusterAuthUserEnabled: Result }>(
    SET_CLUSTER_AUTH_USER_ENABLED
  );
  const [deleteUser] = useMutation<{ deleteClusterAuthUser: Result }>(DELETE_CLUSTER_AUTH_USER);
  const [setGroups] = useMutation<{ setClusterAuthUserGroups: Result }>(
    SET_CLUSTER_AUTH_USER_GROUPS
  );
  const [createGroup] = useMutation<{ createClusterAuthGroup: Result }>(CREATE_CLUSTER_AUTH_GROUP);

  async function write(
    call: () => Promise<Result | undefined | null>,
    fallback: string,
    success: string,
    refresh = false,
    throwOnRefusal = false
  ): Promise<boolean> {
    function refused(reason: string) {
      if (throwOnRefusal) throw new Error(reason);
      toast.error(reason);
      return false;
    }
    if (current.current !== lease || view?.supported !== true || !expectedSource)
      return refused(t("sourceChanged"));
    let result;
    try {
      result = await call();
    } catch (error) {
      return refused(error instanceof Error && error.message ? error.message : fallback);
    }
    if (!result?.ok) return refused(result?.errors?.[0]?.message ?? fallback);
    toast.success(success);
    if (refresh) {
      if (current.current === lease)
        await refetchAfterMutation({ refetch: () => refetch({ clusterId }) }, t("refreshWarning"));
      else toast.warning(t("refreshWarning"));
    }
    return true;
  }
  function knownUser(username: string) {
    return view?.users.find((user) => user.username === username)?.providerUserId || null;
  }
  async function onSetGroups(username: string, add: string[], remove: string[]) {
    if (!knownUser(username)) {
      toast.error(t("sourceChanged"));
      return false;
    }
    return write(
      async () =>
        (
          await setGroups({
            variables: {
              input: {
                clusterId,
                expectedSource,
                expectedUserId: knownUser(username),
                username,
                add,
                remove,
              },
            },
          })
        ).data?.setClusterAuthUserGroups,
      t("groupsFailed"),
      t("groupsAccepted"),
      true
    );
  }
  async function onCreateGroup(name: string) {
    return write(
      async () =>
        (await createGroup({ variables: { input: { clusterId, expectedSource, name } } })).data
          ?.createClusterAuthGroup,
      t("createGroupFailed"),
      t("groupCreated"),
      true
    );
  }
  async function onToggleEnabled(user: AstroliftClusterAuthUser) {
    if (!knownUser(user.username)) {
      toast.error(t("sourceChanged"));
      return;
    }
    await write(
      async () =>
        (
          await setEnabled({
            variables: {
              input: {
                clusterId,
                expectedSource,
                expectedUserId: knownUser(user.username),
                username: user.username,
                enabled: !user.enabled,
              },
            },
          })
        ).data?.setClusterAuthUserEnabled,
      t("enabledFailed"),
      t(user.enabled ? "disabledAccepted" : "enabledAccepted"),
      true
    );
  }
  async function onCreate(input: NewAuthUser) {
    return write(
      async () =>
        (await createUser({ variables: { input: { clusterId, expectedSource, ...input } } })).data
          ?.createClusterAuthUser,
      t("createFailed"),
      t(input.password ? "createdPassword" : "createdInvitation"),
      true
    );
  }
  async function onSetPassword(username: string, password: string, permanent: boolean) {
    if (!knownUser(username)) {
      toast.error(t("sourceChanged"));
      return false;
    }
    return write(
      async () =>
        (
          await setPassword({
            variables: {
              input: {
                clusterId,
                expectedSource,
                expectedUserId: knownUser(username),
                username,
                password,
                permanent,
              },
            },
          })
        ).data?.setClusterAuthUserPassword,
      t("passwordFailed"),
      t("passwordAccepted")
    );
  }
  async function onResetPassword(username: string) {
    if (!knownUser(username)) {
      toast.error(t("sourceChanged"));
      return false;
    }
    return write(
      async () =>
        (
          await resetPassword({
            variables: {
              input: { clusterId, expectedSource, expectedUserId: knownUser(username), username },
            },
          })
        ).data?.resetClusterAuthUserPassword,
      t("resetFailed"),
      t("resetAccepted")
    );
  }
  async function onDelete(username: string) {
    if (!knownUser(username)) throw new Error(t("sourceChanged"));
    await write(
      async () =>
        (
          await deleteUser({
            variables: {
              input: { clusterId, expectedSource, expectedUserId: knownUser(username), username },
            },
          })
        ).data?.deleteClusterAuthUser,
      t("deleteFailed"),
      t("deleteAccepted"),
      true,
      true
    );
  }
  async function onRetry() {
    if (current.current !== lease) return;
    try {
      await refetch({ clusterId });
    } catch {
      /* Keep the query diagnostic visible. */
    }
  }
  return {
    sourceKey,
    reviewedSource: expectedSource !== null,
    view,
    loading,
    error:
      readError?.message ??
      (!loading && data?.astroliftClusterAuthUsers === undefined ? t("unknownSource") : null),
    onRetry,
    onSetGroups,
    onCreateGroup,
    onToggleEnabled,
    onCreate,
    onSetPassword,
    onResetPassword,
    onDelete,
  };
}
