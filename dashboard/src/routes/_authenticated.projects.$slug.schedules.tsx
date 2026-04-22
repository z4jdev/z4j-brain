/**
 * Schedules page - sortable schedule list with DataTable.
 *
 * Features:
 * - Full-text search across name, task
 * - Kind filter (cron/interval/solar/clocked)
 * - Enabled/disabled filter
 * - Sortable columns (name, kind, task, priority, next_run, last_run, total_runs)
 * - Inline enable/disable switch and trigger button
 */
import { useMemo, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { History, Play, RefreshCw, Search, X } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/domain/page-header";
import { TaskPriorityBadge } from "@/components/domain/state-badges";
import { EmptyState } from "@/components/domain/empty-state";
import { DataTable } from "@/components/ui/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useCan } from "@/hooks/use-memberships";
import {
  useSchedules,
  useToggleSchedule,
  useTriggerSchedule,
} from "@/hooks/use-schedules";
import { DateCell } from "@/components/domain/date-cell";
import { ApiError } from "@/lib/api";
import type { ScheduleKind, SchedulePublic } from "@/lib/api-types";
import { cn } from "@/lib/utils";

export const Route = createFileRoute(
  "/_authenticated/projects/$slug/schedules",
)({
  component: SchedulesPage,
});

const SCHEDULE_KINDS: ScheduleKind[] = ["cron", "interval", "solar", "clocked"];

function SchedulesPage() {
  const { slug } = Route.useParams();
  const { data: schedules, isLoading, isFetching, refetch } = useSchedules(slug);
  const toggle = useToggleSchedule(slug);
  const trigger = useTriggerSchedule(slug);

  const [searchQuery, setSearchQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<ScheduleKind | "all">("all");
  const [enabledFilter, setEnabledFilter] = useState<
    "all" | "enabled" | "disabled"
  >("all");

  const activeFilterCount =
    (kindFilter !== "all" ? 1 : 0) + (enabledFilter !== "all" ? 1 : 0);

  const clearFilters = () => {
    setSearchQuery("");
    setKindFilter("all");
    setEnabledFilter("all");
  };

  // Client-side filtering
  const filteredSchedules = useMemo(() => {
    if (!schedules) return [];
    return schedules.filter((s) => {
      if (kindFilter !== "all" && s.kind !== kindFilter) return false;
      if (enabledFilter === "enabled" && !s.is_enabled) return false;
      if (enabledFilter === "disabled" && s.is_enabled) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        if (
          !s.name.toLowerCase().includes(q) &&
          !s.task_name.toLowerCase().includes(q)
        ) {
          return false;
        }
      }
      return true;
    });
  }, [schedules, kindFilter, enabledFilter, searchQuery]);

  async function onToggle(scheduleId: string, enabled: boolean) {
    try {
      await toggle.mutateAsync({ scheduleId, enabled });
      toast.success(enabled ? "schedule enabled" : "schedule disabled");
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : (err as Error).message;
      toast.error(`toggle failed: ${message}`);
    }
  }

  async function onTrigger(scheduleId: string) {
    try {
      await trigger.mutateAsync(scheduleId);
      toast.success("trigger command issued");
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : (err as Error).message;
      toast.error(`trigger failed: ${message}`);
    }
  }

  const canManage = useCan(slug, "manage_schedules");
  const columns = useScheduleColumns({
    onToggle,
    onTrigger,
    triggerPending: trigger.isPending,
    canManage,
  });

  // Filter toolbar - rendered inside the DataTable toolbar slot
  const filterToolbar = (
    <div className="flex items-center gap-3">
      <div className="relative flex-1">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          placeholder="Search schedules..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="h-9 pl-9"
        />
      </div>
      <Select
        value={kindFilter}
        onValueChange={(v) => setKindFilter(v as ScheduleKind | "all")}
      >
        <SelectTrigger className="h-9 w-36 shrink-0">
          <SelectValue placeholder="Kind" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All kinds</SelectItem>
          {SCHEDULE_KINDS.map((k) => (
            <SelectItem key={k} value={k}>
              {k}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={enabledFilter}
        onValueChange={(v) =>
          setEnabledFilter(v as "all" | "enabled" | "disabled")
        }
      >
        <SelectTrigger className="h-9 w-36 shrink-0">
          <SelectValue placeholder="Status" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All</SelectItem>
          <SelectItem value="enabled">Enabled</SelectItem>
          <SelectItem value="disabled">Disabled</SelectItem>
        </SelectContent>
      </Select>
      {/* Always reserve space - invisible when no filters active */}
      <Button
        variant="ghost"
        size="sm"
        className={cn(
          "h-9 shrink-0 gap-1 text-xs text-muted-foreground",
          activeFilterCount === 0 && "pointer-events-none invisible",
        )}
        onClick={clearFilters}
      >
        <X className="size-3" />
        Clear
        <Badge variant="secondary" className="ml-0.5 px-1.5 py-0 text-[10px]">
          {activeFilterCount}
        </Badge>
      </Button>
    </div>
  );

  return (
    <div className="space-y-6 p-4 md:p-6">
      <PageHeader
        title="Schedules"
        icon={History}
        description="enable, disable, and fire schedules out-of-band"
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw
              className={isFetching ? "size-4 animate-spin" : "size-4"}
            />
            Refresh
          </Button>
        }
      />

      {isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      )}
      {schedules && filteredSchedules.length === 0 && (
        <div>
          <div className="flex h-[52px] items-center">
            <div className="w-full">{filterToolbar}</div>
          </div>
          <div className="mt-2 overflow-hidden rounded-lg border">
            <EmptyState
              icon={History}
              title="no schedules match"
              description={
                activeFilterCount > 0 || searchQuery
                  ? "try adjusting your filters or search query"
                  : "schedules your scheduler has published (celery-beat, rq-scheduler, etc.) will sync here once the agent observes them"
              }
            />
          </div>
        </div>
      )}
      {schedules && filteredSchedules.length > 0 && (
        <DataTable
          columns={columns}
          data={filteredSchedules}
          enableSelection
          enableSorting
          totalLabel={`${filteredSchedules.length} schedule${filteredSchedules.length === 1 ? "" : "s"}`}
          toolbar={(ctx) =>
            ctx.selectedCount > 0 ? (
              <div className="flex items-center gap-3 rounded-md bg-primary/10 px-4 py-2">
                <span className="text-sm font-medium">
                  {ctx.selectedCount} selected
                </span>
                <div className="ml-auto flex items-center gap-2">
                  {(["critical", "high", "normal", "low"] as const).map((p) => (
                    <Button
                      key={p}
                      variant="outline"
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => {
                        // TODO: wire to API when schedule priority update endpoint is built
                        window.alert(`Set ${ctx.selectedCount} schedules to ${p} priority (not yet wired)`);
                        ctx.clearSelection();
                      }}
                    >
                      {p}
                    </Button>
                  ))}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={ctx.clearSelection}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              filterToolbar
            )
          }
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------

function useScheduleColumns({
  onToggle,
  onTrigger,
  triggerPending,
  canManage,
}: {
  onToggle: (scheduleId: string, enabled: boolean) => void;
  onTrigger: (scheduleId: string) => void;
  triggerPending: boolean;
  canManage: boolean;
}): ColumnDef<SchedulePublic, unknown>[] {
  return useMemo(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        cell: ({ row }: { row: { original: SchedulePublic } }) => {
          const s = row.original;
          return (
            <div>
              <div className="font-medium">{s.name}</div>
              <div className="font-mono text-xs text-muted-foreground">
                {s.scheduler}
              </div>
            </div>
          );
        },
        enableSorting: true,
      },
      {
        accessorKey: "kind",
        header: "Kind",
        cell: ({ row }: { row: { original: SchedulePublic } }) => (
          <Badge variant="outline">{row.original.kind}</Badge>
        ),
        enableSorting: true,
      },
      {
        id: "expression",
        accessorKey: "expression",
        header: "Expression",
        cell: ({ row }: { row: { original: SchedulePublic } }) => (
          <span className="font-mono text-xs">{row.original.expression}</span>
        ),
        enableSorting: false,
      },
      {
        accessorKey: "task_name",
        header: "Task",
        cell: ({ row }: { row: { original: SchedulePublic } }) => (
          <span className="font-mono text-xs text-muted-foreground">
            {row.original.task_name}
          </span>
        ),
        enableSorting: true,
      },
      {
        accessorKey: "priority",
        header: "Priority",
        cell: ({ row }: { row: { original: SchedulePublic } }) => (
          <TaskPriorityBadge priority={row.original.priority} compact />
        ),
        enableSorting: true,
      },
      {
        accessorKey: "last_run_at",
        header: "Last run",
        cell: ({ row }: { row: { original: SchedulePublic } }) => (
          <DateCell value={row.original.last_run_at} />
        ),
        enableSorting: true,
      },
      {
        accessorKey: "next_run_at",
        header: "Next run",
        cell: ({ row }: { row: { original: SchedulePublic } }) => (
          <DateCell value={row.original.next_run_at} />
        ),
        enableSorting: true,
      },
      {
        accessorKey: "total_runs",
        header: "Runs",
        cell: ({ row }: { row: { original: SchedulePublic } }) => (
          <span className="tabular-nums text-sm">
            {row.original.total_runs}
          </span>
        ),
        enableSorting: true,
      },
      {
        id: "enabled",
        header: "Enabled",
        cell: ({ row }: { row: { original: SchedulePublic } }) => (
          <Switch
            checked={row.original.is_enabled}
            onCheckedChange={(checked) => onToggle(row.original.id, checked)}
            disabled={!canManage}
          />
        ),
        enableSorting: false,
      },
      {
        id: "trigger",
        header: "",
        cell: ({ row }: { row: { original: SchedulePublic } }) =>
          canManage ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => onTrigger(row.original.id)}
              disabled={triggerPending}
            >
              <Play className="size-3" />
              Run
            </Button>
          ) : null,
        enableSorting: false,
      },
    ],
    [onToggle, onTrigger, triggerPending, canManage],
  );
}
