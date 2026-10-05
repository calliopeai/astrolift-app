export function domainDiagnosticReasonKey(reason: string) {
  switch (reason) {
    case "PLATFORM_OPERATOR_REQUIRED":
      return "platformOperatorRequired";
    case "DNS_NO_DATA":
      return "dnsNoData";
    case "ICMP_TIMEOUT":
      return "icmpTimeout";
    default:
      return null;
  }
}
