import { gql } from "@apollo/client";

// Backend surface in `backend/astrolift_forms/schema/queries.py` (#453).
// `status` is a plain string on the wire — Strawberry's enum
// serialization is NAME-not-value, so keeping it as a string preserves
// the lowercase literals (`draft` / `published` / `archived`) the FE
// components rely on.

export const GET_FORM_DEFINITIONS = gql`
  query GetFormDefinitions($status: String) {
    formDefinitions(status: $status) {
      id
      name
      slug
      description
      status
      version
      formType
      isPublic
      publishedAt
      createdAt
      updatedAt
      submissionCount
    }
  }
`;

export const GET_FORM_DEFINITION = gql`
  query GetFormDefinition($slug: String!) {
    formDefinition(slug: $slug) {
      id
      name
      slug
      description
      status
      version
      schema
      formType
      isPublic
      fieldConfig
      logicRules
      scoring
      publishedAt
      createdAt
      updatedAt
      submissionCount
    }
  }
`;

export const GET_FORM_SUBMISSIONS = gql`
  query GetFormSubmissions($slug: String!, $status: String) {
    formSubmissions(slug: $slug, status: $status) {
      id
      payload
      status
      submittedAt
      createdAt
      formName
      formVersion
      formSlug
      submitterDisplayName
      submitterEmail
    }
  }
`;

export const GET_FORM_FIELD_TYPES = gql`
  query GetFormFieldTypes {
    formFieldTypes
  }
`;
