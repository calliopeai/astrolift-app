import { gql } from "@apollo/client";

const FIELDS = gql`
  fragment InstallAlertMailTestFields on InstallAlertMailTest {
    id
    requestId
    version
    eventKind
    transport
    sender
    recipient
    status
    reasonCode
    createdAt
    acceptedAt
    deliveryObserved
  }
`;
export const INSTALL_ALERT_MAIL_SUPPORT = gql`
  query InstallAlertMailSupport($eventKind: String!) {
    installAlertMailSupport(eventKind: $eventKind) {
      allowed
      reason
      transport
      sender
      recipient
      tlsMode
      sourceFingerprint
      checkedAt
    }
  }
`;
export const INSTALL_ALERT_MAIL_HISTORY = gql`
  ${FIELDS}
  query InstallAlertMailHistory($eventKind: String!, $after: String, $limit: Int! = 25) {
    installAlertMailTestsPage(eventKind: $eventKind, after: $after, limit: $limit) {
      items {
        ...InstallAlertMailTestFields
      }
      nextCursor
      totalCount
    }
  }
`;
export const SEND_INSTALL_ALERT_MAIL_TEST = gql`
  ${FIELDS}
  mutation SendInstallAlertMailTest($input: SendInstallAlertMailTestInput!) {
    sendInstallAlertMailTest(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
      }
      data {
        ...InstallAlertMailTestFields
      }
    }
  }
`;
