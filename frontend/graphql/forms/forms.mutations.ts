import { gql } from "@apollo/client";

// Backend in `backend/astrolift_forms/schema/mutations.py` (#453).
// Every mutation returns the canonical `MutationResult { ok, errors, data? }`
// envelope (`errors` is a list of `{ code, message, field }`).

export const CREATE_FORM_DEFINITION = gql`
  mutation CreateFormDefinition($input: FormDefinitionInput!) {
    createFormDefinition(input: $input) {
      ok
      errors {
        code
        field
        message
      }
      data {
        id
        slug
        status
        version
      }
    }
  }
`;

export const UPDATE_FORM_DEFINITION = gql`
  mutation UpdateFormDefinition($input: FormDefinitionUpdateInput!) {
    updateFormDefinition(input: $input) {
      ok
      errors {
        code
        field
        message
      }
      data {
        id
        slug
        status
        version
      }
    }
  }
`;

export const PUBLISH_FORM = gql`
  mutation PublishForm($slug: String!) {
    publishForm(slug: $slug) {
      ok
      errors {
        code
        field
        message
      }
      data {
        id
        slug
        status
        version
        publishedAt
      }
    }
  }
`;

export const ARCHIVE_FORM = gql`
  mutation ArchiveForm($slug: String!) {
    archiveForm(slug: $slug) {
      ok
      errors {
        code
        field
        message
      }
      data {
        id
        slug
        status
      }
    }
  }
`;

export const DELETE_FORM_DEFINITION = gql`
  mutation DeleteFormDefinition($input: DeleteFormDefinitionInput!) {
    deleteFormDefinition(input: $input) {
      ok
      errors {
        code
        field
        message
      }
    }
  }
`;

export const SUBMIT_FORM = gql`
  mutation SubmitForm($slug: String!, $payload: JSON!) {
    submitForm(slug: $slug, payload: $payload) {
      ok
      errors {
        code
        field
        message
      }
      data {
        id
        submittedAt
      }
    }
  }
`;

export const UPDATE_SUBMISSION_STATUS = gql`
  mutation UpdateSubmissionStatus($submissionId: GUID!, $status: String!) {
    updateSubmissionStatus(submissionId: $submissionId, status: $status) {
      ok
      errors {
        code
        field
        message
      }
      data {
        id
        status
      }
    }
  }
`;
