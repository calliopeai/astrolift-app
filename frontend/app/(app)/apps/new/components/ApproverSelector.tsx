"use client";

import {
  ApproverSelectorView,
  type ApproverSelectorProps,
} from "@/components/screens/apps/new/ApproverSelector";
import { useApproverSelector } from "@/components/screens/apps/new/use-approver-selector";

export type { ApproverSelection } from "@/components/screens/apps/new/ApproverSelector";

type Props = Pick<ApproverSelectorProps, "value" | "onChange" | "onValidityChange"> & {
  orgSlug: string;
};

/**
 * Approval-gate picker container: loads the org's members and teams and
 * renders the view. Mounted only while the approval gate is on.
 */
export function ApproverSelector({ orgSlug, ...rest }: Props) {
  return <ApproverSelectorView {...useApproverSelector(orgSlug)} {...rest} />;
}
