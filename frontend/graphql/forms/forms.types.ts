// Types mirror the GraphQL surface in `backend/astrolift_forms/schema/`
// (#453). `errors` uses the canonical envelope `{ code, message, field }`
// shared with every other Astrolift mutation.

export type FormDefinition = {
  id: string;
  name: string;
  slug: string;
  description: string;
  status: string;
  version: number;
  schema: Record<string, unknown>;
  formType?: string;
  isPublic?: boolean;
  fieldConfig?: Record<string, unknown>;
  logicRules?: unknown[];
  scoring?: Record<string, unknown>;
  publishedAt: string | null;
  createdAt: string;
  updatedAt: string;
  submissionCount: number;
};

export type FormSubmission = {
  id: string;
  payload: Record<string, unknown>;
  status: string;
  submittedAt: string;
  createdAt: string;
  formName: string;
  formVersion: number;
  formSlug: string;
  submitterDisplayName: string;
  submitterEmail: string;
};

export type FormDefinitionsData = {
  formDefinitions: FormDefinition[];
};

export type FormDefinitionData = {
  formDefinition: FormDefinition | null;
};

export type FormSubmissionsData = {
  formSubmissions: FormSubmission[];
};

export type FormFieldTypesData = {
  formFieldTypes: string[];
};

export type MutationError = {
  code: string;
  message: string;
  field: string | null;
};

export type MutationEnvelope<T> = {
  ok: boolean;
  errors: MutationError[];
  data: T | null;
};

export type CreateFormDefinitionData = {
  createFormDefinition: MutationEnvelope<{
    id: string;
    slug: string;
    status: string;
    version: number;
  }>;
};

export type UpdateFormDefinitionData = {
  updateFormDefinition: MutationEnvelope<{
    id: string;
    slug: string;
    status: string;
    version: number;
  }>;
};

export type PublishFormData = {
  publishForm: MutationEnvelope<{
    id: string;
    slug: string;
    status: string;
    version: number;
    publishedAt: string | null;
  }>;
};

export type ArchiveFormData = {
  archiveForm: MutationEnvelope<{
    id: string;
    slug: string;
    status: string;
  }>;
};

export type DeleteFormDefinitionData = {
  deleteFormDefinition: MutationEnvelope<null>;
};

export type SubmitFormData = {
  submitForm: MutationEnvelope<{
    id: string;
    submittedAt: string;
  }>;
};

export type UpdateSubmissionStatusData = {
  updateSubmissionStatus: MutationEnvelope<{
    id: string;
    status: string;
  }>;
};
