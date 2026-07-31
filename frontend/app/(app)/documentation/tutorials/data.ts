export type Tutorial = {
  slug: string;
  title: string;
  description: string;
  level: string;
  duration: string;
  steps: {
    title: string;
    body: string;
    code?: string;
  }[];
};

export const tutorials: Tutorial[] = [
  {
    slug: "add-django-app",
    title: "Add a new Django app",
    description:
      "Scaffold a new app, register it in settings, create models with Tracking or BaseCoreModel, wire up admin with BaseCoreAdmin, and connect the schema to config/schema.py.",
    level: "Beginner",
    duration: "15 min",
    steps: [
      {
        title: "Scaffold the app",
        body: "Use the run.sh wrapper to create a new Django app inside the container. This gives you the standard Django app structure.",
        code: `./run.sh manage startapp invoicing`,
      },
      {
        title: "Register in settings",
        body: 'Add your new app to INSTALLED_APPS in config/settings.py. Place it before the "End of Astrolift" comment so it\'s grouped with the platform apps.',
        code: `INSTALLED_APPS = [
    # ...
    "invoicing",
    # End of Astrolift
]`,
      },
      {
        title: "Create your model",
        body: "Inherit from Tracking for audit trails, or BaseCoreModel if you also need guid, name, slug, and description. Never expose integer PKs — use guid or Relay global IDs.",
        code: `from core.models import BaseCoreModel
from django.db import models


class Invoice(BaseCoreModel):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    status = models.CharField(max_length=20, default="draft")`,
      },
      {
        title: "Create and run migrations",
        body: "Generate the migration file and apply it. You can scope the migration to your app.",
        code: `make migrations app=invoicing
make migrate`,
      },
      {
        title: "Register the admin",
        body: "Inherit from BaseCoreAdmin to get import/export, audit field handling, and created_by/updated_by tracking for free.",
        code: `from core.utils.admin import BaseCoreAdmin
from django.contrib import admin

from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(BaseCoreAdmin):
    list_display = ("name", "slug", "amount", "status", "created_at")
    search_fields = ("name", "slug")`,
      },
      {
        title: "Create the schema directory",
        body: "Create invoicing/schema/ with __init__.py, types.py, queries.py, and mutations.py. Then wire Query and Mutation into config/schema.py using Strawberry's merge_types or by adding fields to the root types.",
        code: `# invoicing/schema/__init__.py
from .mutations import Mutation
from .queries import Query

__all__ = ["Query", "Mutation"]`,
      },
      {
        title: "Write a test",
        body: "Use schema.execute_sync() with a StrawberryContext for GraphQL tests. Set up an org, user, and membership in setUp().",
        code: `from django.test import TestCase
from config.schema import schema
from core.schema.context import StrawberryContext
from unittest.mock import MagicMock


class InvoiceTest(TestCase):

    def setUp(self):
        from organization.models import Organization, OrganizationMember
        from django.contrib.auth.models import User

        self.org = Organization.objects.create(name="TestOrg")
        self.user = User.objects.create_superuser(
            username="test", email="t@t.com", password="x"
        )
        OrganizationMember.objects.create(
            organization=self.org, member=self.user, is_active=True
        )
        self.user.profile.active_organization = self.org
        self.user.profile.save()

    def _context(self):
        request = MagicMock()
        request.user = self.user
        request.session = {}
        request.headers = {}
        return StrawberryContext(request)`,
      },
    ],
  },
  {
    slug: "graphql-query",
    title: "Build a GraphQL query",
    description:
      "Define a Strawberry type with permission-filtered querysets, write a resolver with auth checks, add it to the schema, and test with schema.execute_sync() in Django TestCase.",
    level: "Beginner",
    duration: "20 min",
    steps: [
      {
        title: "Define the Strawberry type",
        body: "Create a type in your app's schema/types.py. Use strawberry_django.type and implement get_queryset with permission_filtered_queryset to enforce row-level access control.",
        code: `# invoicing/schema/types.py
import strawberry_django
from strawberry.types import Info
from core.schema.common import permission_filtered_queryset
from invoicing.models import Invoice


@strawberry_django.type(Invoice)
class InvoiceType:

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return permission_filtered_queryset(queryset, info)`,
      },
      {
        title: "Write the query resolver",
        body: "Auth check goes at the top of every resolver — no exceptions. Use info.context.user to check authentication, and info.context.organization for org-scoped queries.",
        code: `# invoicing/schema/queries.py
import strawberry
from strawberry.types import Info
from graphql import GraphQLError
from invoicing.models import Invoice
from .types import InvoiceType


@strawberry.type
class Query:

    @strawberry.field
    def invoices(self, info: Info, search: str = "") -> list[InvoiceType]:
        if not info.context.user.is_authenticated:
            raise GraphQLError("Authentication required")
        qs = Invoice.objects.filter(deleted_at__isnull=True)
        if search:
            qs = qs.filter(name__icontains=search)
        return qs

    @strawberry.field
    def invoice(self, info: Info, guid: str) -> InvoiceType | None:
        if not info.context.user.is_authenticated:
            raise GraphQLError("Authentication required")
        return Invoice.objects.filter(
            guid=guid, deleted_at__isnull=True
        ).first()`,
      },
      {
        title: "Wire into the schema",
        body: "Import your Query into config/schema.py and merge it with the root Query type.",
        code: `# config/schema.py
from invoicing.schema import Query as InvoicingQuery

# Add InvoicingQuery to the merge_types call or
# extend the root Query class`,
      },
      {
        title: "Test with execute_sync",
        body: "Use schema.execute_sync() with a mocked context. Assert no errors and check the returned data structure.",
        code: `def test_list_invoices(self):
    Invoice.objects.create(
        name="INV-001", amount="100.00",
        due_date="2026-04-01", created_by=self.user,
    )
    result = schema.execute_sync(
        '{ invoices { name amount status } }',
        context_value=self._context(),
    )
    self.assertIsNone(result.errors)
    self.assertEqual(len(result.data["invoices"]), 1)
    self.assertEqual(result.data["invoices"][0]["name"], "INV-001")`,
      },
    ],
  },
  {
    slug: "create-mutation",
    title: "Create a mutation with MutationResult",
    description:
      "Use restricted_serializer_mutate to build a mutation that validates input, checks permissions via the P enum, returns ok/errors, and sets created_by/updated_by automatically.",
    level: "Intermediate",
    duration: "25 min",
    steps: [
      {
        title: "Define permissions",
        body: "Add your model's permissions in config/permissions.py using ModelPermissions. Then run make perms to regenerate the P enum in config/roles_gen.py.",
        code: `# config/permissions.py
from config.permissions import ModelPermissions, FieldPermissions
from config.roles_gen import P


class InvoicePermissions(ModelPermissions):
    model = FieldPermissions(
        view=P.INVOICE_VIEW,
        add=P.INVOICE_ADD,
        change=P.INVOICE_CHANGE,
        delete=P.INVOICE_DELETE,
    )`,
      },
      {
        title: "Create a serializer",
        body: "Write a DRF serializer for input validation. restricted_serializer_mutate uses this to validate and save.",
        code: `# invoicing/serializers.py
from rest_framework import serializers
from .models import Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["name", "amount", "due_date", "status"]`,
      },
      {
        title: "Write the mutation",
        body: "Always return MutationResult (ok + errors). Check permissions at the top using the P enum before calling restricted_serializer_mutate.",
        code: `# invoicing/schema/mutations.py
import strawberry
from strawberry.types import Info
from core.schema.common import MutationResult
from core.schema.mutations.base import restricted_serializer_mutate
from invoicing.models import Invoice
from invoicing.serializers import InvoiceSerializer


@strawberry.type
class Mutation:

    @strawberry.mutation
    def create_invoice(
        self, info: Info, name: str, amount: str, due_date: str
    ) -> MutationResult:
        Invoice.p("model").add.check(info.context.user)
        return restricted_serializer_mutate(
            InvoiceSerializer, Invoice, info,
            data={"name": name, "amount": amount, "due_date": due_date},
        )

    @strawberry.mutation
    def update_invoice(
        self, info: Info, guid: str, status: str
    ) -> MutationResult:
        Invoice.p("model").change.check(info.context.user)
        instance = Invoice.objects.get(guid=guid)
        return restricted_serializer_mutate(
            InvoiceSerializer, Invoice, info,
            instance=instance,
            data={"status": status},
        )`,
      },
      {
        title: "Test the mutation",
        body: "Execute the mutation via schema.execute_sync and verify ok is True and no errors are returned.",
        code: `def test_create_invoice(self):
    result = schema.execute_sync(
        """mutation {
            createInvoice(
                name: "INV-002"
                amount: "250.00"
                dueDate: "2026-05-01"
            ) { ok errors { field messages } }
        }""",
        context_value=self._context(),
    )
    self.assertIsNone(result.errors)
    self.assertTrue(result.data["createInvoice"]["ok"])`,
      },
    ],
  },
  {
    slug: "server-paginated-table",
    title: "Wire up a server-paginated data table",
    description:
      "Use useCursorTable with DataTable to build a list surface with server-side pagination, debounced search, and the required empty, loading, and error states.",
    level: "Intermediate",
    duration: "25 min",
    steps: [
      {
        title: "Write the page query",
        body: "Return a Page envelope — items, nextCursor, totalCount — and accept limit, after, and search. This is the shape every Astrolift list query speaks; the backend helper keyset_page() produces it.",
        code: `// graphql/invoices/invoices.queries.ts
import { gql } from "@apollo/client";

export const LIST_INVOICES_PAGE = gql\`
  query ListInvoicesPage(
    $limit: Int
    $after: String
    $search: String
    $status: String
  ) {
    astroliftInvoicesPage(
      limit: $limit
      after: $after
      search: $search
      status: $status
    ) {
      items {
        id
        name
        amount
        status
        dueDate
      }
      nextCursor
      totalCount
    }
  }
\\\`;`,
      },
      {
        title: "Define the columns",
        body: "A column is an id, a header, and a cell function. Give width classes so the loading skeletons match the real rows. Add sortKey only for columns the server can actually sort on.",
        code: `// app/(app)/invoices/columns.tsx
"use client";
import type { Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";

export type Invoice = {
  id: string;
  name: string;
  amount: string;
  status: string;
  dueDate: string;
};

export const invoiceColumns: Column<Invoice>[] = [
  {
    id: "name",
    header: "Name",
    cell: (row) => <span className="font-medium">{row.name}</span>,
  },
  {
    id: "amount",
    header: "Amount",
    align: "right",
    width: "w-32",
    cell: (row) => <span className="tabular-nums">{row.amount}</span>,
  },
  {
    id: "status",
    header: "Status",
    width: "w-28",
    cell: (row) => <Badge variant="outline">{row.status}</Badge>,
  },
  {
    id: "dueDate",
    header: "Due",
    width: "w-32",
    cell: (row) => row.dueDate,
  },
];`,
      },
      {
        title: "Drive it with useCursorTable",
        body: "extract picks the Page envelope out of the response. searchVariable names the query's search argument — only pass it if the query really has one, or the table renders a box that does nothing. urlKey persists the search term in the URL.",
        code: `// app/(app)/invoices/invoices-client.tsx
"use client";
import { FileTextIcon } from "lucide-react";
import { DataTable, useCursorTable } from "@/components/data-table";

import { LIST_INVOICES_PAGE } from "@/graphql/invoices/invoices.queries";
import { invoiceColumns, type Invoice } from "./columns";

type Resp = {
  astroliftInvoicesPage: {
    items: Invoice[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
};

export function InvoicesClient() {
  const table = useCursorTable<Invoice>({
    query: LIST_INVOICES_PAGE,
    extract: (data) => (data as Resp | undefined)?.astroliftInvoicesPage,
    searchVariable: "search",
    urlKey: "inv",
    fetchPolicy: "cache-and-network",
  });

  return (
    <DataTable
      label="Invoices"
      controller={table}
      columns={invoiceColumns}
      getRowId={(row) => row.id}
      rowHref={(row) => \\\`/invoices/\\\${row.id}\\\`}
      searchPlaceholder="Search invoices…"
      empty={{
        icon: <FileTextIcon className="size-5" />,
        title: "No invoices yet",
        description: "Invoices appear here once your first one is issued.",
      }}
      emptyFiltered={{
        title: "No matching invoices",
        description: "No invoice matches that search. Try a different term.",
      }}
    />
  );
}`,
      },
      {
        title: "Render it",
        body: "No Suspense boundary is needed: the controller reads the URL through window.location on mount and writes it back with history.replaceState, so it never touches useSearchParams.",
        code: `// app/(app)/invoices/page.tsx
import { InvoicesClient } from "./invoices-client";

export default function InvoicesPage() {
  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-xl font-semibold">Invoices</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Manage your invoices.
        </p>
      </div>
      <InvoicesClient />
    </div>
  );
}`,
      },
      {
        title: "What you did NOT have to write",
        body: "Pagination controls, the debounce, the skeleton rows, the empty and no-match states, the error-and-retry row, and the search box are all in DataTable. If you find yourself reaching for @/components/ui/table directly, the lint rule astroliftData/no-raw-table will stop you — that is the signal you are re-deriving one of these. See the 'Data surfaces' section of frontend/bootstrap.md.",
        code: `# The rules that matter, in short:
#
#   - Pagination is server-side. Always. Fetching a capped list and
#     paging it in the browser hides every row past the cap.
#   - No search box unless the query has a search argument.
#   - empty and emptyFiltered are different states with different copy.
#   - rowHref for rows that navigate; onRowActivate for rows that do
#     something else. Never both.
#   - Destructive actions confirm through ConfirmDialog, never
#     window.confirm.`,
      },
    ],
  },
  {
    slug: "permission-guards",
    title: "Add permission guards to a page",
    description:
      "Define a PermissionSlug, use requirePermission in a server component for instant 404, and wrap client sections with PermissionGuard or withPermissionAuthenticationRequired.",
    level: "Intermediate",
    duration: "20 min",
    steps: [
      {
        title: "Add the slug to the enum",
        body: "All slugs live in the PermissionSlug const enum. This is the single source of truth — both server and client code reference it.",
        code: `// graphql/permissions/permissions.types.ts
export const enum PermissionSlug {
  SecuredPage = "secured-page",
  Invoicing = "invoicing",  // add your slug here
}`,
      },
      {
        title: "Guard a server component page",
        body: "Call requirePermission at the top of a Server Component. If the backend component is inactive, Next.js returns a 404 immediately — no HTML is streamed.",
        code: `// app/(app)/invoices/page.tsx
import { requirePermission, PermissionSlug } from "@/lib/permissions";

export default async function InvoicesPage() {
  await requirePermission(PermissionSlug.Invoicing);

  return <div>Protected content</div>;
}`,
      },
      {
        title: "Guard a client component section",
        body: "Use PermissionGuard to conditionally render JSX, or withPermissionAuthenticationRequired as an HOC. Import PermissionSlug from the types file to avoid server-only imports in the client bundle.",
        code: `// Wrapper approach
import { PermissionGuard } from "@/components/PermissionGuard";
import { PermissionSlug } from "@/graphql/permissions/permissions.types";

<PermissionGuard permission={PermissionSlug.Invoicing}>
  <InvoiceDashboard />
</PermissionGuard>

// HOC approach
import { withPermissionAuthenticationRequired } from "@/components/PermissionGuard";

function InvoiceDashboard() { /* ... */ }
export default withPermissionAuthenticationRequired(
  InvoiceDashboard,
  PermissionSlug.Invoicing
);`,
      },
      {
        title: "Use checkPermission for conditional logic",
        body: "When you need a boolean instead of an instant 404, use checkPermission. It swallows network errors and returns false — safe for degraded-mode rendering.",
        code: `import { checkPermission, PermissionSlug } from "@/lib/permissions";

const canSeeInvoices = await checkPermission(PermissionSlug.Invoicing);

if (canSeeInvoices) {
  // show invoice widget on dashboard
}`,
      },
    ],
  },
  {
    slug: "celery-task",
    title: "Build a Celery background task",
    description:
      "Create a retryable task in tasks.py, trigger it from a mutation, schedule it with beat, and monitor it through the Flower dashboard at localhost:5555.",
    level: "Advanced",
    duration: "30 min",
    steps: [
      {
        title: "Create the task",
        body: "Tasks live in appname/tasks.py. Use @app.task() and import models inside the function to avoid circular imports. For retryable tasks, use bind=True and max_retries.",
        code: `# invoicing/tasks.py
from config.celery import app


@app.task(bind=True, max_retries=3)
def send_invoice_email(self, invoice_id):
    from invoicing.models import Invoice
    from django.core.mail import send_mail

    try:
        invoice = Invoice.objects.get(id=invoice_id)
        send_mail(
            subject=f"Invoice {invoice.name}",
            message=f"Amount due: {invoice.amount}",
            from_email="billing@example.com",
            recipient_list=[invoice.created_by.email],
        )
        invoice.status = "sent"
        invoice.save()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)`,
      },
      {
        title: "Trigger from a mutation",
        body: "Call .delay() to enqueue the task asynchronously. The mutation returns immediately while Celery processes in the background.",
        code: `# invoicing/schema/mutations.py
@strawberry.mutation
def send_invoice(self, info: Info, guid: str) -> MutationResult:
    if not info.context.user.is_authenticated:
        raise GraphQLError("Authentication required")
    invoice = Invoice.objects.get(guid=guid)
    Invoice.p("model").change.check(info.context.user)

    from invoicing.tasks import send_invoice_email
    send_invoice_email.delay(invoice.id)

    return MutationResult(ok=True, errors=[])`,
      },
      {
        title: "Schedule with beat",
        body: "For recurring tasks, register them with Celery beat using DatabaseScheduler. Schedules are managed in the Django admin.",
        code: `# config/celery.py — beat schedule example
app.conf.beat_schedule = {
    "send-overdue-reminders": {
        "task": "invoicing.tasks.send_overdue_reminders",
        "schedule": crontab(hour=9, minute=0),  # daily at 9am
    },
}`,
      },
      {
        title: "Monitor with Flower",
        body: "Flower runs at localhost:5555 and shows task history, worker status, and active queues. Use it to verify your tasks are being picked up and completing successfully.",
        code: `# Flower is already running in the stack
# Open http://localhost:5555 in your browser

# To check Celery worker logs:
make logs  # then look for celery-worker output`,
      },
    ],
  },
  {
    slug: "rule-engine",
    title: "Configure the rule engine",
    description:
      "Define conditions and actions in core_rule_engine, attach them to model signals, and let the Celery-backed evaluator run your business logic automatically on state changes.",
    level: "Advanced",
    duration: "45 min",
    steps: [
      {
        title: "Understand the rule model",
        body: "The rule engine consists of three parts: Conditions (when to fire), Actions (what to do), and Rules that combine them. Rules are attached to models via the RuleProviderMixin, and evaluation is triggered by Django model signals.",
        code: `# Core components in core_rule_engine/models.py
#
# Condition  — a predicate (e.g. "status == overdue")
# Action     — a side effect (e.g. "send reminder email")
# Rule       — links conditions to actions with AND/OR logic
#
# RuleProviderMixin — added to your model to enable
#                     signal-triggered evaluation`,
      },
      {
        title: "Add RuleProviderMixin to your model",
        body: "Mix RuleProviderMixin into your model class. This registers post_save and post_delete signals that trigger rule evaluation via Celery.",
        code: `from core.models import BaseCoreModel
from core_rule_engine.mixins import RuleProviderMixin


class Invoice(RuleProviderMixin, BaseCoreModel):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default="draft")`,
      },
      {
        title: "Define conditions and actions",
        body: "Create conditions and actions in the Django admin or programmatically. Conditions are evaluated against the model instance; actions fire when all conditions pass.",
        code: `# Example: programmatic setup in a migration or seed
from core_rule_engine.models import Condition, Action, Rule

condition = Condition.objects.create(
    name="Invoice is overdue",
    field="status",
    operator="equals",
    value="overdue",
)

action = Action.objects.create(
    name="Send overdue reminder",
    action_type="celery_task",
    task_name="invoicing.tasks.send_overdue_reminders",
)

rule = Rule.objects.create(name="Overdue reminder rule")
rule.conditions.add(condition)
rule.actions.add(action)`,
      },
      {
        title: "Evaluation flow",
        body: "When a model with RuleProviderMixin is saved, the post_save signal dispatches rule evaluation to a Celery task. Each rule's conditions are checked against the instance, and matching actions are executed asynchronously.",
        code: `# The flow is automatic once RuleProviderMixin is added:
#
# 1. invoice.save()
# 2. post_save signal fires
# 3. RuleProviderMixin dispatches evaluate_rules.delay(instance_id)
# 4. Celery worker evaluates all rules for that model
# 5. Matching actions execute (email, task, webhook, etc.)
#
# Monitor in Flower at http://localhost:5555`,
      },
    ],
  },
  {
    slug: "opensearch-indexing",
    title: "Set up OpenSearch indexing",
    description:
      "Create a document class, wire up signals for incremental indexing, run make reindex for a full rebuild, and query the search endpoint from GraphQL resolvers.",
    level: "Advanced",
    duration: "40 min",
    steps: [
      {
        title: "Create a document class",
        body: "Define an OpenSearch document that maps your model fields to search fields. The document class specifies which fields are indexed and how they're analyzed.",
        code: `# invoicing/documents.py
from django_opensearch_dsl import Document, fields
from django_opensearch_dsl.registries import registry
from .models import Invoice


@registry.register_document
class InvoiceDocument(Document):
    name = fields.TextField(
        attr="name",
        fields={"keyword": fields.KeywordField()},
    )
    status = fields.KeywordField()
    amount = fields.FloatField()

    class Index:
        name = "invoices"
        settings = {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        }

    class Django:
        model = Invoice
        fields = ["guid", "due_date", "created_at"]`,
      },
      {
        title: "Wire up signals for incremental indexing",
        body: "django-opensearch-dsl automatically registers post_save and post_delete signals when you use @registry.register_document. Each save updates the index document; each delete removes it.",
        code: `# Signals are automatic with @registry.register_document
# But you can also trigger manual updates:

from invoicing.documents import InvoiceDocument

# Update a single document
doc = InvoiceDocument()
doc.update(invoice_instance)

# Or in bulk
InvoiceDocument().update(Invoice.objects.filter(status="paid"))`,
      },
      {
        title: "Rebuild the full index",
        body: "Use make reindex to tear down and rebuild all OpenSearch indices. This is useful after adding new fields or changing analyzers.",
        code: `# Full rebuild of all indices
make reindex

# OpenSearch is available at http://localhost:9200
# Check index health:
curl http://localhost:9200/_cat/indices?v`,
      },
      {
        title: "Query from a GraphQL resolver",
        body: "Use the document's search() method in a resolver. Return model instances by fetching the PKs from the search results and querying the database.",
        code: `# invoicing/schema/queries.py
@strawberry.field
def search_invoices(
    self, info: Info, query: str
) -> list[InvoiceType]:
    if not info.context.user.is_authenticated:
        raise GraphQLError("Authentication required")

    from invoicing.documents import InvoiceDocument

    search = InvoiceDocument.search().query(
        "multi_match",
        query=query,
        fields=["name", "name.keyword"],
    )
    response = search.execute()
    ids = [hit.meta.id for hit in response]
    return Invoice.objects.filter(
        id__in=ids, deleted_at__isnull=True
    )`,
      },
    ],
  },
];
