import { gql } from "@apollo/client";

/**
 * Only the app's deploy-approval setting, through `updateApp` (so
 * `app.update` on the app), for Easy setup's guided approval (#2272).
 */
export const UPDATE_APP_DEPLOY_APPROVAL = gql`
  mutation UpdateAppDeployApproval($input: UpdateAppInput!) {
    updateApp(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        requiresApproval
        approverTeamId
        approverUserIds
        minimumApprovals
        version
      }
    }
  }
`;
