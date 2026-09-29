"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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

import type { NewAuthUser } from "./types";

type Result = { ok: boolean; errors?: { message: string }[] | null };

function firstError(r: Result | undefined, fallback: string): string {
  return r?.errors?.[0]?.message ?? fallback;
}

/**
 * The users of the cluster's central auth (#2131): who can sign in to the
 * apps behind it. Passwords are sent once; nothing the server returns
 * carries one. The data half of AuthUsersView.
 */
export function useAuthUsers(clusterId: string) {
  const { data, loading, refetch } = useQuery<{
    astroliftClusterAuthUsers: AstroliftClusterAuthUsers | null;
  }>(CLUSTER_AUTH_USERS, { variables: { clusterId }, fetchPolicy: "cache-and-network" });
  const refetchOpts = { onCompleted: () => void refetch() };

  const [createUser] = useMutation<{ createClusterAuthUser: Result }>(
    CREATE_CLUSTER_AUTH_USER,
    refetchOpts
  );
  const [setPassword] = useMutation<{ setClusterAuthUserPassword: Result }>(
    SET_CLUSTER_AUTH_USER_PASSWORD
  );
  const [resetPassword] = useMutation<{ resetClusterAuthUserPassword: Result }>(
    RESET_CLUSTER_AUTH_USER_PASSWORD
  );
  const [setEnabled] = useMutation<{ setClusterAuthUserEnabled: Result }>(
    SET_CLUSTER_AUTH_USER_ENABLED,
    refetchOpts
  );
  const [deleteUser] = useMutation<{ deleteClusterAuthUser: Result }>(
    DELETE_CLUSTER_AUTH_USER,
    refetchOpts
  );
  const [setGroups] = useMutation<{ setClusterAuthUserGroups: Result }>(
    SET_CLUSTER_AUTH_USER_GROUPS,
    refetchOpts
  );
  const [createGroup] = useMutation<{ createClusterAuthGroup: Result }>(
    CREATE_CLUSTER_AUTH_GROUP,
    refetchOpts
  );

  async function onSetGroups(username: string, add: string[], remove: string[]) {
    const { data } = await setGroups({
      variables: { input: { clusterId, username, add, remove } },
    });
    if (!data?.setClusterAuthUserGroups.ok) {
      toast.error(firstError(data?.setClusterAuthUserGroups, "Group change failed."));
    }
  }

  async function onCreateGroup(name: string): Promise<boolean> {
    const { data } = await createGroup({ variables: { input: { clusterId, name } } });
    if (!data?.createClusterAuthGroup.ok) {
      toast.error(firstError(data?.createClusterAuthGroup, "Couldn't create group."));
      return false;
    }
    return true;
  }

  async function onToggleEnabled(user: AstroliftClusterAuthUser) {
    const { data } = await setEnabled({
      variables: { input: { clusterId, username: user.username, enabled: !user.enabled } },
    });
    if (data?.setClusterAuthUserEnabled.ok) {
      toast.success(user.enabled ? "User disabled." : "User enabled.");
    } else {
      toast.error(firstError(data?.setClusterAuthUserEnabled, "Change failed."));
    }
  }

  async function onCreate(input: NewAuthUser): Promise<boolean> {
    const { data } = await createUser({ variables: { input: { clusterId, ...input } } });
    if (!data?.createClusterAuthUser.ok) {
      toast.error(firstError(data?.createClusterAuthUser, "Couldn't create the user."));
      return false;
    }
    toast.success(
      input.password
        ? "User created with the password you set."
        : "User created. They were emailed a temporary password."
    );
    return true;
  }

  async function onSetPassword(
    username: string,
    password: string,
    permanent: boolean
  ): Promise<boolean> {
    const { data } = await setPassword({
      variables: { input: { clusterId, username, password, permanent } },
    });
    if (!data?.setClusterAuthUserPassword.ok) {
      toast.error(firstError(data?.setClusterAuthUserPassword, "Couldn't set the password."));
      return false;
    }
    toast.success("Password set.");
    return true;
  }

  async function onResetPassword(username: string): Promise<boolean> {
    const { data } = await resetPassword({ variables: { input: { clusterId, username } } });
    if (!data?.resetClusterAuthUserPassword.ok) {
      toast.error(firstError(data?.resetClusterAuthUserPassword, "Couldn't send the reset."));
      return false;
    }
    toast.success("Reset code sent to the user.");
    return true;
  }

  /** Throws on failure, so the confirm dialog stays open with the error. */
  async function onDelete(username: string) {
    const { data } = await deleteUser({ variables: { input: { clusterId, username } } });
    if (!data?.deleteClusterAuthUser.ok) {
      throw new Error(firstError(data?.deleteClusterAuthUser, "Delete failed."));
    }
    toast.success("User deleted.");
  }

  return {
    view: data?.astroliftClusterAuthUsers ?? null,
    loading,
    onSetGroups,
    onCreateGroup,
    onToggleEnabled,
    onCreate,
    onSetPassword,
    onResetPassword,
    onDelete,
  };
}
