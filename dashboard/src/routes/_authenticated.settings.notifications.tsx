/**
 * User settings - notification subscriptions.
 *
 * Shows every subscription the user has across all their projects,
 * grouped by project. Each row = one (project, trigger) subscription
 * with filters, channel set, mute state, and active toggle.
 */
import { useMemo, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Bell, BellOff, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useConfirm } from "@/components/domain/confirm-dialog";
import { EmptyState } from "@/components/domain/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { PageHeader } from "@/components/domain/page-header";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useCreateUserSubscription,
  useDeleteUserSubscription,
  useUpdateUserSubscription,
  useUserChannels,
  useUserSubscriptions,
  type TriggerType,
  type UserSubscription,
} from "@/hooks/use-notifications";
import { useProjects } from "@/hooks/use-projects";
import type { ProjectPublic } from "@/lib/api-types";

export const Route = createFileRoute("/_authenticated/settings/notifications")({
  component: NotificationsPage,
});

const TRIGGERS: { value: TriggerType; label: string }[] = [
  { value: "task.failed", label: "Task failed" },
  { value: "task.succeeded", label: "Task succeeded" },
  { value: "task.retried", label: "Task retried" },
  { value: "task.slow", label: "Task slow" },
  { value: "agent.offline", label: "Agent offline" },
  { value: "agent.online", label: "Agent online" },
];

const PRIORITIES = ["critical", "high", "normal", "low"] as const;

function triggerLabel(t: TriggerType | string): string {
  return TRIGGERS.find((x) => x.value === t)?.label ?? t;
}

function NotificationsPage() {
  const { data: subs, isLoading, isFetching } = useUserSubscriptions();
  const { data: projects } = useProjects();
  const deleteSub = useDeleteUserSubscription();
  const updateSub = useUpdateUserSubscription();
  const [dialogOpen, setDialogOpen] = useState(false);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const projectMap = useMemo(
    () => new Map((projects ?? []).map((p) => [p.id, p])),
    [projects],
  );

  const grouped = useMemo(() => {
    const groups = new Map<string, UserSubscription[]>();
    for (const sub of subs ?? []) {
      const list = groups.get(sub.project_id) ?? [];
      list.push(sub);
      groups.set(sub.project_id, list);
    }
    return Array.from(groups.entries());
  }, [subs]);

  return (
    <div className="space-y-6">
      {confirmDialog}
      <PageHeader
        title={
          <>
            Notifications
            {isFetching && !isLoading && (
              <RefreshCw className="ml-2 inline size-3 animate-spin text-muted-foreground" />
            )}
          </>
        }
        description="Choose which events you want to hear about, and where to deliver them."
        actions={
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button size="sm" disabled={!projects || projects.length === 0}>
                <Plus className="size-4" />
                New subscription
              </Button>
            </DialogTrigger>
            <DialogContent>
              <CreateSubscriptionDialog
                projects={projects ?? []}
                onCreated={() => setDialogOpen(false)}
              />
            </DialogContent>
          </Dialog>
        }
      />

      {isLoading && <Skeleton className="h-32 w-full" />}

      {subs && subs.length === 0 && (
        <EmptyState
          icon={Bell}
          title="No subscriptions yet"
          description="Create a subscription to start receiving notifications when tasks fail, agents go offline, etc."
        />
      )}

      {grouped.length > 0 && (
        <div className="space-y-6">
          {grouped.map(([projectId, projectSubs]) => {
            const project = projectMap.get(projectId);
            return (
              <Card key={projectId} className="overflow-hidden p-0">
                <div className="border-b bg-muted/30 px-4 py-2">
                  {project ? (
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold">
                        {project.name}
                      </span>
                      <Badge variant="outline" className="font-mono text-xs">
                        {project.slug}
                      </Badge>
                    </div>
                  ) : (
                    <code className="text-xs">{projectId}</code>
                  )}
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Trigger</TableHead>
                      <TableHead>Channels</TableHead>
                      <TableHead>Filters</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Active</TableHead>
                      <TableHead className="w-10" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {projectSubs.map((sub) => (
                      <TableRow key={sub.id}>
                        <TableCell>
                          <Badge variant="outline">
                            {triggerLabel(sub.trigger)}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {channelSummary(sub)}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {filterSummary(sub.filters)}
                        </TableCell>
                        <TableCell>
                          {sub.muted_until &&
                          new Date(sub.muted_until).getTime() >
                            Date.now() ? (
                            <Badge variant="muted">
                              <BellOff className="size-3" />
                              muted
                            </Badge>
                          ) : (
                            <span className="text-xs text-muted-foreground">
                              -
                            </span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Switch
                            checked={sub.is_active}
                            onCheckedChange={(checked) => {
                              updateSub.mutate(
                                {
                                  id: sub.id,
                                  body: { is_active: checked },
                                },
                                {
                                  onSuccess: () =>
                                    toast.success(
                                      checked
                                        ? "Subscription enabled"
                                        : "Subscription disabled",
                                    ),
                                  onError: (err) => {
                                    const msg =
                                      err instanceof Error
                                        ? err.message
                                        : "Request failed";
                                    toast.error(msg);
                                  },
                                },
                              );
                            }}
                          />
                        </TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Delete ${triggerLabel(sub.trigger)} subscription`}
                            className="text-muted-foreground hover:text-destructive"
                            onClick={() =>
                              confirm({
                                title: "Delete subscription",
                                description: (
                                  <>
                                    Stop receiving the{" "}
                                    <code>{triggerLabel(sub.trigger)}</code>{" "}
                                    notification for this project?
                                  </>
                                ),
                                confirmLabel: "Delete",
                                onConfirm: () =>
                                  deleteSub.mutate(sub.id, {
                                    onSuccess: () =>
                                      toast.success("Subscription deleted"),
                                    onError: (err) => {
                                      const msg =
                                        err instanceof Error
                                          ? err.message
                                          : "Request failed";
                                      toast.error(msg);
                                    },
                                  }),
                              })
                            }
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

function channelSummary(sub: UserSubscription): string {
  const parts: string[] = [];
  if (sub.in_app) parts.push("In-app");
  const extra =
    sub.project_channel_ids.length + sub.user_channel_ids.length;
  if (extra > 0) {
    parts.push(`${extra} channel${extra === 1 ? "" : "s"}`);
  }
  return parts.length > 0 ? parts.join(" + ") : "-";
}

function filterSummary(filters: Record<string, unknown>): string {
  const keys = Object.keys(filters);
  if (keys.length === 0) return "-";
  const bits: string[] = [];
  if (Array.isArray(filters.priority)) {
    bits.push(`priority: ${(filters.priority as string[]).join(",")}`);
  }
  if (typeof filters.task_name_pattern === "string") {
    bits.push(`name: ${filters.task_name_pattern}`);
  }
  return bits.length > 0 ? bits.join("; ") : JSON.stringify(filters);
}

// ---------------------------------------------------------------------------
// Create subscription dialog
// ---------------------------------------------------------------------------

function CreateSubscriptionDialog({
  projects,
  onCreated,
}: {
  projects: ProjectPublic[];
  onCreated: () => void;
}) {
  const createSub = useCreateUserSubscription();
  const { data: userChannels } = useUserChannels();

  const [projectId, setProjectId] = useState(projects[0]?.id ?? "");
  const [trigger, setTrigger] = useState<TriggerType>("task.failed");
  const [priorities, setPriorities] = useState<string[]>([]);
  const [taskNamePattern, setTaskNamePattern] = useState("");
  const [inApp, setInApp] = useState(true);
  const [selectedUserChannels, setSelectedUserChannels] = useState<string[]>(
    [],
  );
  const [cooldown, setCooldown] = useState("300");

  const togglePriority = (p: string) => {
    setPriorities((cur) =>
      cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p],
    );
  };

  const toggleUserChannel = (id: string) => {
    setSelectedUserChannels((cur) =>
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!projectId) {
      toast.error("Please pick a project");
      return;
    }
    const filters: Record<string, unknown> = {};
    if (priorities.length > 0) filters.priority = priorities;
    if (taskNamePattern.trim())
      filters.task_name_pattern = taskNamePattern.trim();

    createSub.mutate(
      {
        project_id: projectId,
        trigger,
        filters,
        in_app: inApp,
        user_channel_ids: selectedUserChannels,
        cooldown_seconds: parseInt(cooldown, 10) || 0,
      },
      {
        onSuccess: () => {
          toast.success("Subscription created");
          onCreated();
        },
        onError: (err) => toast.error(`Failed: ${err.message}`),
      },
    );
  };

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>New Subscription</DialogTitle>
      </DialogHeader>
      <div className="mt-4 space-y-4">
        <div className="space-y-2">
          <Label htmlFor="new-sub-project">Project</Label>
          <Select value={projectId} onValueChange={setProjectId}>
            <SelectTrigger id="new-sub-project">
              <SelectValue placeholder="Select a project" />
            </SelectTrigger>
            <SelectContent>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name} ({p.slug})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label htmlFor="new-sub-trigger">Trigger</Label>
          <Select
            value={trigger}
            onValueChange={(v) => setTrigger(v as TriggerType)}
          >
            <SelectTrigger id="new-sub-trigger">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TRIGGERS.map((t) => (
                <SelectItem key={t.value} value={t.value}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Filters */}
        <div
          className="space-y-2"
          role="group"
          aria-labelledby="new-sub-priority-heading"
        >
          <span
            id="new-sub-priority-heading"
            className="text-sm font-medium leading-none"
          >
            Priority filter
          </span>
          <div className="flex flex-wrap gap-3">
            {PRIORITIES.map((p) => (
              <label
                key={p}
                className="flex items-center gap-1.5 text-sm cursor-pointer"
              >
                <Checkbox
                  checked={priorities.includes(p)}
                  onCheckedChange={() => togglePriority(p)}
                />
                <span>{p}</span>
              </label>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            Leave empty to fire on all priorities.
          </p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="new-sub-task-pattern">
            Task name pattern (optional)
          </Label>
          <Input
            id="new-sub-task-pattern"
            placeholder="e.g. app.tasks.critical.*"
            value={taskNamePattern}
            onChange={(e) => setTaskNamePattern(e.target.value)}
          />
        </div>

        {/* Deliver via */}
        <div
          className="space-y-2"
          role="group"
          aria-labelledby="new-sub-deliver-heading"
        >
          <span
            id="new-sub-deliver-heading"
            className="text-sm font-medium leading-none"
          >
            Deliver via
          </span>
          <div className="space-y-2 rounded-md border p-3">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <Checkbox
                checked={inApp}
                onCheckedChange={(c) => setInApp(c === true)}
              />
              <span>In-app (the bell menu)</span>
            </label>

            {userChannels && userChannels.length > 0 && (
              <div className="space-y-1.5 border-t pt-2">
                <p className="text-xs font-medium text-muted-foreground">
                  Your personal channels
                </p>
                {userChannels.map((ch) => (
                  <label
                    key={ch.id}
                    className="flex items-center gap-2 text-sm cursor-pointer"
                  >
                    <Checkbox
                      checked={selectedUserChannels.includes(ch.id)}
                      onCheckedChange={() => toggleUserChannel(ch.id)}
                    />
                    <span>{ch.name}</span>
                    <Badge variant="outline" className="text-[10px]">
                      {ch.type}
                    </Badge>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="new-sub-cooldown">Cooldown (seconds)</Label>
          <Input
            id="new-sub-cooldown"
            type="number"
            min={0}
            max={86400}
            value={cooldown}
            onChange={(e) => setCooldown(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Minimum interval between firings for the same task name. 0 = no
            cooldown.
          </p>
        </div>
      </div>
      <DialogFooter className="mt-6">
        <Button type="submit" disabled={createSub.isPending}>
          {createSub.isPending ? "Creating..." : "Create Subscription"}
        </Button>
      </DialogFooter>
    </form>
  );
}
