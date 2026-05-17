import { gql } from "@apollo/client";

export const CREATE_WORKFLOW = gql`
  mutation CreateWorkflowDefinition(
    $name: String!
    $slug: String!
    $modelLabel: String!
    $states: JSON!
    $transitions: JSON!
    $description: String
    $isEnabled: Boolean
  ) {
    createWorkflowDefinition(
      name: $name
      slug: $slug
      modelLabel: $modelLabel
      states: $states
      transitions: $transitions
      description: $description
      isEnabled: $isEnabled
    ) {
      ok
      errors {
        field
        messages
      }
    }
  }
`;

export const UPDATE_WORKFLOW = gql`
  mutation UpdateWorkflowDefinition(
    $slug: String!
    $name: String
    $description: String
    $modelLabel: String
    $states: JSON
    $transitions: JSON
    $isEnabled: Boolean
  ) {
    updateWorkflowDefinition(
      slug: $slug
      name: $name
      description: $description
      modelLabel: $modelLabel
      states: $states
      transitions: $transitions
      isEnabled: $isEnabled
    ) {
      ok
      errors {
        field
        messages
      }
    }
  }
`;

export const DELETE_WORKFLOW = gql`
  mutation DeleteWorkflowDefinition($slug: String!) {
    deleteWorkflowDefinition(slug: $slug) {
      ok
      errors {
        field
        messages
      }
    }
  }
`;

export const START_WORKFLOW = gql`
  mutation StartWorkflow($workflowSlug: String!, $modelLabel: String!, $objectId: Int!) {
    startWorkflow(workflowSlug: $workflowSlug, modelLabel: $modelLabel, objectId: $objectId) {
      ok
      instanceId
      errors {
        field
        messages
      }
    }
  }
`;

export const TRANSITION_WORKFLOW = gql`
  mutation TransitionWorkflow($instanceId: ID!, $toState: String!, $note: String) {
    transitionWorkflow(instanceId: $instanceId, toState: $toState, note: $note) {
      ok
      errors {
        field
        messages
      }
    }
  }
`;

// ─── Temporal instance viewer (#437) ────────────────────────────────────

export const CANCEL_WORKFLOW_INSTANCE = gql`
  mutation CancelWorkflowInstance($workflowId: String!) {
    cancelWorkflowInstance(workflowId: $workflowId) {
      ok
      errors {
        field
        messages
      }
    }
  }
`;

export const TERMINATE_WORKFLOW_INSTANCE = gql`
  mutation TerminateWorkflowInstance($workflowId: String!, $reason: String!) {
    terminateWorkflowInstance(workflowId: $workflowId, reason: $reason) {
      ok
      errors {
        field
        messages
      }
    }
  }
`;

export const SIGNAL_WORKFLOW_INSTANCE = gql`
  mutation SignalWorkflowInstance($workflowId: String!, $signalName: String!, $payload: JSON) {
    signalWorkflowInstance(workflowId: $workflowId, signalName: $signalName, payload: $payload) {
      ok
      errors {
        field
        messages
      }
    }
  }
`;
