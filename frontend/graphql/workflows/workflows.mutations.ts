import { gql } from "@apollo/client";

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
