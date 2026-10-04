"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import {
  CHANGE_TEAM_MEMBERSHIP,
  GET_TEAM_MEMBERSHIP_REVIEW,
} from "@/graphql/identity/team-memberships.queries";
import type {
  ChangeTeamMembershipMutation,
  ChangeTeamMembershipMutationVariables,
  GetTeamMembershipReviewQuery,
  GetTeamMembershipReviewQueryVariables,
} from "@/graphql/__generated__/operations";
import type { MembershipPhase, MembershipReview, MembershipRow } from "./TeamMembershipPanels";

interface Target {
  teamId: string;
  orgMemberId: string;
  kind: "ADD" | "REMOVE";
}
export function useMembershipAction(onRefresh: () => Promise<unknown>) {
  const client = useApolloClient(),
    t = useTranslations("teams.memberships");
  const [target, setTarget] = useState<Target | null>(null);
  const [review, setReview] = useState<MembershipReview | null>(null);
  const [roleId, setRoleId] = useState("");
  const [phase, setPhase] = useState<MembershipPhase>("reading");
  const [error, setError] = useState<string | null>(null);
  const lifecycle = useRef({ active: false, revision: 0 });
  const busy = useRef(false);
  const abort = useRef<AbortController | null>(null);
  const pending = useRef<ChangeTeamMembershipMutationVariables["input"] | null>(null);
  useLayoutEffect(() => {
    const state = lifecycle.current;
    state.active = true;
    return () => {
      state.active = false;
      state.revision++;
      abort.current?.abort();
    };
  }, []);
  const current = (version: number) =>
    lifecycle.current.active && lifecycle.current.revision === version;
  async function read(next: Target, nextRole: string) {
    const version = ++lifecycle.current.revision;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setTarget(next);
    setRoleId(nextRole);
    setReview(null);
    setError(null);
    setPhase("reading");
    try {
      const result = await client.query<
        GetTeamMembershipReviewQuery,
        GetTeamMembershipReviewQueryVariables
      >({
        query: GET_TEAM_MEMBERSHIP_REVIEW,
        variables: { ...next, roleId: nextRole || null },
        fetchPolicy: "no-cache",
        context: { queryDeduplication: false, fetchOptions: { signal: controller.signal } },
      });
      if (!current(version)) return;
      const reviewed = result.data?.astroliftTeamMembershipReview;
      if (
        !reviewed ||
        reviewed.kind !== next.kind ||
        reviewed.membership.team.id !== next.teamId ||
        reviewed.membership.person.orgMemberId !== next.orgMemberId
      ) {
        setError(t("unavailable"));
        setPhase("refused");
        return;
      }
      setReview(reviewed);
      setPhase("review");
    } catch (err) {
      if (current(version)) {
        setError(err instanceof Error ? err.message : t("unavailable"));
        setPhase("refused");
      }
    }
  }
  async function send(input: ChangeTeamMembershipMutationVariables["input"]) {
    if (busy.current) return;
    busy.current = true;
    const version = lifecycle.current.revision;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setPhase("writing");
    setError(null);
    try {
      const result = await client.mutate<
        ChangeTeamMembershipMutation,
        ChangeTeamMembershipMutationVariables
      >({
        mutation: CHANGE_TEAM_MEMBERSHIP,
        variables: { input },
        fetchPolicy: "no-cache",
        context: { fetchOptions: { signal: controller.signal } },
      });
      if (!current(version)) return;
      const envelope = result.data?.changeAstroliftTeamMembership;
      if (envelope?.ok === false) {
        pending.current = null;
        setPhase("refused");
        setError(envelope.errors[0]?.message ?? t("refused"));
        return;
      }
      const committed = envelope?.data;
      if (
        !envelope?.ok ||
        !committed?.committed ||
        committed.requestId !== input.requestId ||
        committed.teamId !== input.teamId ||
        committed.orgMemberId !== input.orgMemberId
      ) {
        setPhase("uncertain");
        return;
      }
      pending.current = null;
      setPhase("committed");
      try {
        await onRefresh();
      } catch {
        if (current(version)) setPhase("refreshFailed");
      }
    } catch {
      if (current(version)) setPhase("uncertain");
    } finally {
      busy.current = false;
    }
  }
  const open = (next: Target) => {
    if (pending.current || busy.current) return;
    void read(next, "");
  };
  return {
    target,
    review,
    roleId,
    phase,
    error,
    openAdd: (teamId: string, orgMemberId: string) => open({ teamId, orgMemberId, kind: "ADD" }),
    openRemove: (row: MembershipRow) =>
      open({ teamId: row.team.id, orgMemberId: row.person.orgMemberId, kind: "REMOVE" }),
    onRole: (id: string) => {
      if (target && phase === "review" && !pending.current && !busy.current) void read(target, id);
    },
    onConfirm: () => {
      if (!target || !review || phase !== "review" || busy.current || pending.current) return;
      if (target.kind === "ADD" && !review.roles.some((role) => role.id === roleId)) return;
      const input = {
        requestId: crypto.randomUUID(),
        kind: target.kind,
        teamId: target.teamId,
        orgMemberId: target.orgMemberId,
        roleId: target.kind === "ADD" ? roleId : null,
        expectedSource: review.expectedSource,
      };
      pending.current = input;
      void send(input);
    },
    onRetryOriginal: () => {
      if (pending.current && phase === "uncertain") void send(pending.current);
    },
    onReviewAgain: () => {
      if (target && !pending.current && !busy.current) void read(target, "");
    },
    close: () => {
      if (pending.current || busy.current) return;
      lifecycle.current.revision++;
      abort.current?.abort();
      setTarget(null);
      setReview(null);
    },
    canClose: phase !== "writing" && phase !== "uncertain",
  };
}
