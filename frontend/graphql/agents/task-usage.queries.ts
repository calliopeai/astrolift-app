import { gql } from "@apollo/client";

/** Exact-task reported usage; actualCost remains unknown until billing is reconciled. */
export const AGENT_TASK_USAGE = gql`
  query AgentTaskUsage($id: ID!) {
    agentTask(id: $id) {
      usageSnapshot {
        taskId
        taskVersion
        taskStatus
        asOf
        aggregationScope
        linkedRunId
        linkedRunRetryCount
        inputTokens {
          value
          unit
          source
          sourceRecordedAt
          completeness
          reason
        }
        outputTokens {
          value
          unit
          source
          sourceRecordedAt
          completeness
          reason
        }
        duration {
          value
          unit
          source
          sourceRecordedAt
          completeness
          reason
        }
        reportedCost {
          amount
          currency
          source
          sourceRecordedAt
          completeness
          reconciliation
          reason
        }
        actualCost {
          amount
          currency
          source
          sourceRecordedAt
          completeness
          reconciliation
          reason
        }
        model
        modelSource
        modelSourceRecordedAt
      }
    }
  }
`;
