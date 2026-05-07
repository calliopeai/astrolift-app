import { PageShell } from "@/components/PageShell";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export const metadata = { title: "Policies · Settings · Astrolift" };

export default function PoliciesSettingsPage() {
  return (
    <PageShell
      title="ABAC policies"
      description="Runtime predicates evaluated after RBAC. Policies can only deny — they never grant beyond the role bindings."
    >
      <Card>
        <CardHeader>
          <CardTitle>Policy editor</CardTitle>
          <CardDescription>
            ABAC conditions: time windows, IP allowlists, approval requirements,
            environment match, MFA freshness, device assertions.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-muted-foreground space-y-3 text-sm">
          <p>
            The Policy model is in the API today (
            <code className="font-mono text-xs">
              astrolift_identity.Policy
            </code>
            ); the runtime evaluator runs after RBAC during permission
            resolution. The list/create/edit GraphQL mutations land alongside
            the policy-as-code DSL in the next milestone.
          </p>
          <p>
            Until then, policies are seeded directly by the install playbook
            for org-wide rules (deny production deploys outside business hours,
            require fresh MFA on secret reads, etc).
          </p>
        </CardContent>
      </Card>
    </PageShell>
  );
}
