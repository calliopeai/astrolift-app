# astrolift_graphql: the list contract

Every list query converges on one contract (spec 44 §5.1, #2149). This package
holds the pieces; a list declares its fields and calls them. The reference
application is `astroliftAppsPage` / `astroliftMyAppsPage` in
`astrolift_registry/schema/queries.py` (look for "The list contract on the
Apps list").

## The contract

| Argument | Shape | Helper |
|---|---|---|
| `filter` | a per-list strawberry `input`, one field per declared filter | `filter_q`, `filter_values` (`filtering.py`) |
| `search` | one string | `search_q(term, *fields, prefix=(...))` (`pagination.py`) |
| `sort` | multi-key string, `-deployed,name` (the frontend's `formatSort`) | `resolve_list_sort` (`sorting.py`) |
| `page` / `pageSize` | numbered lists | `numbered_page` (`pagination.py`) |
| `first` / `after` (or `limit` / `cursor`) | cursor lists | `keyset_page` (`pagination.py`) |

The result is `PageType[T]` (or a list's own page type with the same fields):
`items`, `totalCount`, and either `nextCursor` (cursor) or `page` and
`pageSize` (numbered).

Each list declares one paging mode (spec 44 §5.1): numbered where the set is
small and stable (apps, agents, workflows, skills, members, teams, projects),
cursor where it is large or keeps growing (runs, deployments, job runs, audit).

## Applying it to a list

```python
from astrolift_graphql import FilterField, SortKey, filter_q, numbered_page, resolve_list_sort, search_q

# 1. Declare what the list sorts on. Keys are the frontend's column keys.
_THING_SORTS = {
    "name": SortKey(Lower("name")),
    "created": SortKey("created_at"),
    "started": SortKey("started_at", nulls_low=True),  # nullable: say where NULLs go
}

# 2. Declare what it filters on. A list value becomes __in; `me=True` swaps
#    "me" for the viewer.
_THING_FILTERS = {
    "status": FilterField("status"),
    "project": FilterField("project__slug"),
    "started_by": FilterField("triggered_by_user_id", me=True),
    "cluster": FilterField(q=lambda slugs: Q(pk__in=...)),  # anything else: a Q
}

# 3. The resolver: scope first (deny by default), then search, filter, page.
qs = Thing.objects.filter(organization_id=org_id) if org_id is not None else Thing.objects.none()
if search and search.strip():
    qs = qs.filter(search_q(search.strip(), "name", "slug", prefix=("guid", "commit_sha")))
qs = qs.filter(filter_q(filter, _THING_FILTERS, me=viewer_id))
order_by = resolve_list_sort(sort, _THING_SORTS, default="-created")
return numbered_page(qs, order_by=order_by, page=page, page_size=page_size).map(thing_to_type)
```

## Rules

- **Add, do not break.** Existing arguments keep working. A list that already
  pages by cursor gains `page` / `pageSize` / `sort` / `filter` as optional
  arguments; the Apps page takes the numbered path when any of `page`,
  `pageSize` or `sort` is given and the old cursor walk otherwise.
- **Sort in SQL or not at all.** A derived column (a status, the latest
  deploy) becomes an annotation (`Case`, `Subquery`, `Coalesce`) so OFFSET and
  `totalCount` stay exact. Sorting or filtering the rows in Python after the
  query makes every page boundary wrong.
- **An undeclared sort key is refused** (`UnsupportedSort`), never dropped.
  `resolve_list_sort` appends the pk as a tiebreak, so the order is total.
- **Filter on an annotation with a non-null value.** Negating a filter on a
  nullable annotation drops the NULL rows (SQL three-valued logic, and Django
  does not add the `IS NULL` arm for annotations). Wrap the condition in a
  `Case(..., default=Value(False))`, as `_failing` does.
- **Something only Python can answer** (the Apps topology kind) is computed
  once over the filtered set and folded back in as `pk__in`, never per page.
- **Fields a row needs for its columns** are fetched in bulk per page, one
  query per source, never per row. Pin it with a query-count test that
  compares a small page to a large one.
- **Multi-key `sort` on a cursor list is not built yet.** A keyset seek needs
  a WHERE clause per key combination, so cursor lists keep sorting through a
  `ListSortKey` enum and `keyset_page`'s `sort_field` / `cursor_scope`.

`ListSortInput` / `SortDirection` are the typed form of a sort spec
(`[{key, direction}]`); `resolve_list_sort` accepts either. The Apps page
takes the string, which is what the URL and `formatSort` carry.
