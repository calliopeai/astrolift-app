import { gql } from "@apollo/client";

const NITRO_TASK_READINESS_FIELDS = gql`
  fragment NitroTaskReadinessFields on AstroliftNitroTaskReadiness {
    clusterId
    version
    sourceToken
    checkedAt
    namespace
    source
    modes {
      mode
      enabled
      state
      checks {
        code
        state
        message
        evidence
      }
    }
  }
`;

export const GET_NITRO_TASK_READINESS = gql`
  query NitroTaskReadiness($clusterId: GUID!) {
    astroliftNitroTaskReadiness(clusterId: $clusterId) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ...NitroTaskReadinessFields
      }
    }
  }
  ${NITRO_TASK_READINESS_FIELDS}
`;

export const CONFIGURE_NITRO_TASKS = gql`
  mutation ConfigureNitroTasks($input: ConfigureNitroTasksInput!) {
    configureNitroTasks(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ...NitroTaskReadinessFields
      }
    }
  }
  ${NITRO_TASK_READINESS_FIELDS}
`;
