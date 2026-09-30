"use client";

import { toast } from "sonner";

import {
  useArchiveForm,
  useFormDefinition,
  useFormSubmissions,
  usePublishForm,
} from "@/graphql/forms/forms.hooks";

/** A form definition, its submissions, and the publish / archive mutations. */
export function useFormDetail(slug: string) {
  const { form, loading, error } = useFormDefinition(slug);
  const {
    submissions,
    loading: submissionsLoading,
    error: submissionsError,
    refetch: refetchSubmissions,
  } = useFormSubmissions(slug);
  const [publishForm] = usePublishForm();
  const [archiveForm] = useArchiveForm();

  const onPublish = async (): Promise<boolean> => {
    const { data } = await publishForm({ variables: { slug } });
    if (data?.publishForm?.ok) {
      toast.success("Form published");
      return true;
    }
    toast.error("Failed to publish");
    return false;
  };

  const onArchive = async (): Promise<boolean> => {
    const { data } = await archiveForm({ variables: { slug } });
    if (data?.archiveForm?.ok) {
      toast.success("Form archived");
      return true;
    }
    toast.error("Failed to archive");
    return false;
  };

  return {
    slug,
    form,
    loading,
    error,
    submissions,
    submissionsLoading,
    submissionsError,
    onRetrySubmissions: () => {
      void refetchSubmissions().catch(() => {});
    },
    onPublish,
    onArchive,
  };
}
