"use client";
import { useState } from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { ModelConnectionRequestsScreen } from "./ModelConnectionRequestsScreen";
import { useModelConnectionRequests } from "./use-model-connection-requests";
export function ModelConnectionRequestsClient() {
  const { org } = useActiveOrg(),
    { user } = useMe();
  return <Context key={`${user?.id}:${org?.id}`} />;
}
function Context() {
  const [review, setReview] = useState(false);
  return <Page key={String(review)} review={review} onReview={setReview} />;
}
function Page({ review, onReview }: { review: boolean; onReview: (review: boolean) => void }) {
  const props = useModelConnectionRequests(review, onReview);
  return <ModelConnectionRequestsScreen {...props} />;
}
