export function domainDiagnosticReasonKey(reason: string) {
  switch (reason) {
    case "PLATFORM_OPERATOR_REQUIRED":
      return "platformOperatorRequired";
    case "DNS_NO_DATA":
      return "dnsNoData";
    case "ICMP_TIMEOUT":
      return "icmpTimeout";
    case "DNS_ANSWER":
      return "dnsAnswerHelp";
    case "PUBLIC_DELEGATION_MATCH":
      return "delegationMatch";
    case "INTERNAL_DNS_PROBE_NOT_CONFIGURED":
      return "internalDnsUnavailable";
    default:
      return null;
  }
}
