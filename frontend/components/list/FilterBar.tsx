"use client";

import { useTranslations } from "next-intl";

import {
  ArrowDownIcon,
  ArrowUpIcon,
  ChevronDownIcon,
  Columns3Icon,
  PlusIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import * as React from "react";

import type { SortState } from "@/components/data-table";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ViewToggle } from "@/components/ViewToggle";
import { useDebounce } from "@/hooks/use-debounce";
import { cn } from "@/lib/utils";

import {
  extractFilterTokens,
  type ListField,
  type ListFieldOption,
  type ListStateController,
} from "./list-state";

/**
 * The one filter bar on every list (spec 44 §5.1):
 *
 *   [ ⌕ Search runs, ids…  / ] [ + Filter ] status: failed ×  agent: sup… ×  Clear
 *                                    [ Sort: Started ↓ ▾ ] [ ⋯ columns ] [ list|cards ] [ ⋯ ]
 *
 * Search is focused with `/` and debounced; `field:value` tokens typed in it
 * become chips. The sort menu shows only in card view, which has no headers,
 * and drives the same sort as the headers.
 */

export interface FilterBarColumn {
  id: string;
  label: string;
  sortKey?: string;
  /** False keeps the column out of the chooser (the first, linking column). */
  hideable?: boolean;
}

export interface FilterBarProps {
  list: ListStateController;
  /** The table's columns, for the chooser and the card view's sort menu. */
  columns: FilterBarColumn[];
  /** Hide the list|cards toggle on a list with no card renderer. */
  cards?: boolean;
  /** The overflow `⋯` menu, e.g. CSV export on Admin lists. */
  menu?: React.ReactNode;
  /** Before the search box: an embedded list's view picker. */
  leading?: React.ReactNode;
  className?: string;
}

const SEARCH_DEBOUNCE_MS = 250;

export function FilterBar({
  list,
  columns,
  cards = true,
  menu,
  leading,
  className,
}: FilterBarProps) {
  const t = useTranslations("shared.list");
  const { definition: def, state } = list;
  const chips = Object.entries(state.filters);
  const sortable = columns.filter((c) => c.sortKey);
  const hideable = columns.filter((c) => c.hideable !== false);

  return (
    <div className={cn("flex min-w-0 flex-wrap items-center gap-2", className)}>
      {leading}
      <SearchBox list={list} />
      <AddFilter fields={def.fields} onAdd={list.setFilter} />

      {chips.map(([key, value]) => {
        const field = def.fields.find((f) => f.key === key);
        const label = field?.options?.find((o) => o.value === value)?.label ?? value;
        return (
          <span
            key={key}
            className="bg-muted inline-flex max-w-full min-w-0 items-center gap-1 rounded-md border py-0.5 pr-0.5 pl-2 text-sm"
          >
            <span className="text-muted-foreground shrink-0">{field?.label ?? key}:</span>
            <span className="min-w-0 truncate font-mono" title={value}>
              {label}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="size-5 shrink-0"
              aria-label={t("removeFilter", { field: field?.label ?? key, value: label })}
              onClick={() => list.setFilter(key, null)}
            >
              <XIcon className="size-3" />
            </Button>
          </span>
        );
      })}
      {list.isFiltered && (
        <Button variant="ghost" size="sm" onClick={list.clearFilters}>
          {t("clear")}
        </Button>
      )}

      <div className="ml-auto flex shrink-0 items-center gap-2">
        {list.mode === "card" && sortable.length > 0 && (
          <SortMenu sort={state.sort} columns={sortable} onChange={list.setSort} />
        )}
        {hideable.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" aria-label={t("chooseColumns")}>
                <Columns3Icon className="size-4" />
                <span className="hidden lg:inline">{t("columns")}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-44">
              <DropdownMenuLabel>{t("columns")}</DropdownMenuLabel>
              {hideable.map((c) => (
                <DropdownMenuCheckboxItem
                  key={c.id}
                  checked={!list.hiddenColumns.includes(c.id)}
                  onCheckedChange={() => list.toggleColumn(c.id)}
                  onSelect={(e) => e.preventDefault()}
                >
                  {c.label}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
        {cards && <ViewToggle mode={list.mode} onChange={list.setMode} />}
        {menu}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function SearchBox({ list }: { list: ListStateController }) {
  const t = useTranslations("shared.list");
  const { definition: def, state } = list;
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [text, setText] = React.useState(state.q);
  const debounced = useDebounce(text, SEARCH_DEBOUNCE_MS);

  // The URL is the source of truth: a back button, a view tab or Clear
  // changes `q` underneath the box, and the box follows.
  const committed = React.useRef(state.q);
  React.useEffect(() => {
    if (state.q !== committed.current) {
      committed.current = state.q;
      setText(state.q);
    }
  }, [state.q]);

  const commit = React.useCallback(
    (value: string, complete: boolean) => {
      const { text: rest, filters } = extractFilterTokens(def, value, { complete });
      const hasTokens = Object.keys(filters).length > 0;
      if (hasTokens) setText(rest);
      // While typing, a trailing `field:…` stays in the box and is not yet
      // searched as text either: it is about to become a chip.
      const trailing = /(?:^|\s)([A-Za-z][\w-]*):\S*$/.exec(rest);
      const pending =
        !complete &&
        trailing &&
        def.fields.some((f) =>
          [f.key, f.label].some((n) => n.toLowerCase() === trailing[1].toLowerCase())
        );
      const q = pending ? rest.slice(0, trailing.index).trim() : rest;
      if (q === committed.current && !hasTokens) return;
      committed.current = q;
      if (hasTokens) list.applySearch(q, filters);
      else list.setSearch(q);
    },
    [def, list]
  );

  React.useEffect(() => {
    commit(debounced, false);
    // Commit on the debounced value only; `commit` changes with every state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  // `/` focuses search from anywhere on the page, unless the person is
  // already typing into something.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="relative min-w-48 flex-1 sm:max-w-xs">
      <SearchIcon
        aria-hidden
        className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2"
      />
      <Input
        ref={inputRef}
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(text, true);
          if (e.key === "Escape" && text) setText("");
        }}
        placeholder={def.searchPlaceholder}
        aria-label={def.searchPlaceholder}
        aria-keyshortcuts="/"
        className="pr-8 pl-8"
      />
      {text ? (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => {
            setText("");
            commit("", true);
          }}
          className="absolute top-1/2 right-1 size-6 -translate-y-1/2"
          aria-label={t("clearSearch")}
        >
          <XIcon className="size-3.5" />
        </Button>
      ) : (
        <kbd
          aria-hidden
          className="text-muted-foreground text-2xs pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 rounded-sm border px-1 font-mono"
        >
          /
        </kbd>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function AddFilter({
  fields,
  onAdd,
}: {
  fields: ListField[];
  onAdd: (key: string, value: string) => void;
}) {
  const t = useTranslations("shared.list");
  const [open, setOpen] = React.useState(false);
  const [field, setField] = React.useState<ListField | null>(null);

  if (fields.length === 0) return null;
  const close = () => {
    setOpen(false);
    setField(null);
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setField(null);
      }}
    >
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          <PlusIcon className="size-4" />
          {t("filter")}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 p-1">
        {field ? (
          <ValuePicker
            field={field}
            onBack={() => setField(null)}
            onPick={(value) => {
              onAdd(field.key, value);
              close();
            }}
          />
        ) : (
          <ul role="listbox" aria-label={t("filterBy")} className="flex flex-col">
            {fields.map((f) => (
              <li key={f.key}>
                <button
                  type="button"
                  role="option"
                  aria-selected={false}
                  onClick={() => setField(f)}
                  className="hover:bg-muted focus-visible:bg-muted flex w-full items-center justify-between rounded-sm px-2 py-1.5 text-left text-sm outline-none"
                >
                  <span className="min-w-0 truncate">{f.label}</span>
                  <span className="text-muted-foreground font-mono text-xs">{f.key}:</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}

function ValuePicker({
  field,
  onBack,
  onPick,
}: {
  field: ListField;
  onBack: () => void;
  onPick: (value: string) => void;
}) {
  const t = useTranslations("shared.list");
  const [query, setQuery] = React.useState("");
  const debounced = useDebounce(query, SEARCH_DEBOUNCE_MS);
  const [found, setFound] = React.useState<ListFieldOption[] | null>(null);
  const [failed, setFailed] = React.useState(false);

  React.useEffect(() => {
    if (!field.async) return;
    let live = true;
    field.async(debounced).then(
      (options) => {
        if (!live) return;
        setFound(options);
        setFailed(false);
      },
      () => live && setFailed(true)
    );
    return () => {
      live = false;
    };
  }, [field, debounced]);

  const options = field.options
    ? field.options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase()))
    : (found ?? []);

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1 px-1 pt-1">
        <Button variant="ghost" size="sm" onClick={onBack} aria-label={t("backToFields")}>
          ‹
        </Button>
        <span className="text-sm font-medium">{field.label}</span>
      </div>
      <form
        className="px-1"
        onSubmit={(e) => {
          e.preventDefault();
          const exact = options.find((o) => o.label.toLowerCase() === query.toLowerCase());
          if (exact) onPick(exact.value);
          else if (!field.options && query.trim()) onPick(query.trim());
        }}
      >
        <Input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={field.options ? t("findValue", { field: field.label }) : t("typeValue")}
          aria-label={t("fieldValue", { field: field.label })}
          className="h-8"
        />
      </form>
      {failed && <p className="text-danger px-2 py-1 text-sm">{t("valuesFailed")}</p>}
      {field.async && found === null && !failed && (
        <p className="text-muted-foreground px-2 py-1 text-sm">{t("loading")}</p>
      )}
      <ul
        role="listbox"
        aria-label={t("fieldValues", { field: field.label })}
        className="flex max-h-64 flex-col overflow-y-auto"
      >
        {options.map((o) => (
          <li key={o.value}>
            <button
              type="button"
              role="option"
              aria-selected={false}
              onClick={() => onPick(o.value)}
              className="hover:bg-muted focus-visible:bg-muted w-full min-w-0 truncate rounded-sm px-2 py-1.5 text-left font-mono text-sm outline-none"
            >
              {o.label}
            </button>
          </li>
        ))}
        {!field.options && !field.async && query.trim() && (
          <li>
            <button
              type="button"
              role="option"
              aria-selected={false}
              onClick={() => onPick(query.trim())}
              className="hover:bg-muted w-full min-w-0 truncate rounded-sm px-2 py-1.5 text-left text-sm outline-none"
            >
              {t.rich("fieldEquals", {
                field: field.label,
                value: query.trim(),
                valueText: (chunks) => <span className="font-mono">{chunks}</span>,
              })}
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------

function SortMenu({
  sort,
  columns,
  onChange,
}: {
  sort: SortState[];
  columns: FilterBarColumn[];
  onChange: (sort: SortState[]) => void;
}) {
  const t = useTranslations("shared.list");
  const primary = sort[0];
  const current = columns.find((c) => c.sortKey === primary?.key);
  const Arrow = primary?.dir === "asc" ? ArrowUpIcon : ArrowDownIcon;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          <span className="text-muted-foreground">{t("sortPrefix")}</span>
          {current?.label ?? primary?.key ?? t("defaultSort")}
          {primary && (
            <Arrow
              className="size-3.5"
              aria-label={t(primary.dir === "asc" ? "ascending" : "descending")}
            />
          )}
          <ChevronDownIcon className="size-3.5 opacity-60" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-44">
        <DropdownMenuLabel>{t("sortBy")}</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={primary?.key}
          onValueChange={(key) => onChange([{ key, dir: primary?.dir ?? "desc" }])}
        >
          {columns.map((c) => (
            <DropdownMenuRadioItem key={c.id} value={c.sortKey!}>
              {c.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          value={primary?.dir}
          onValueChange={(dir) =>
            primary &&
            onChange([{ key: primary.key, dir: dir as SortState["dir"] }, ...sort.slice(1)])
          }
        >
          <DropdownMenuRadioItem value="asc">{t("ascending")}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="desc">{t("descending")}</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
