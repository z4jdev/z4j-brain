/**
 * Project settings - Default subscriptions (admin onboarding templates).
 *
 * Admins define what notifications new members start with. These rows
 * are copied into each member's personal subscription list when they
 * join; existing members are not retroactively affected.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { BellRing, Lock, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useConfirm } from "@/components/domain/confirm-dialog";
import { EmptyState } from "@/components/domain/empty-state";
import { useIsProjectAdmin } from "@/hooks/use-memberships";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useChannels,
  useCreateDefaultSubscription,
  useDefaultSubscriptions,
  useDeleteDefaultSubscription,
  type NotificationChannel,
  type TriggerType,
} from "@/hooks/use-notifications";

export const Route = createFileRoute(
  "/_authenticated/projects/$slug/settings/defaults",
)({
  component: DefaultsPage,
});

const TRIGGERS: { value: TriggerType; label: string }[] = [
  { value: "task.failed", label: "Task failed" },
  { value: "task.succeeded", label: "Task succeeded" },
  { value: "task.retried", label: "Task retried" },
  { value: "task.slow", label: "Task slow" },
  { value: "agent.offline", label: "Agent offline" },
  { value: "agent.online", label: "Agent online" },
];

function triggerLabel(t: TriggerType | string): string {
  return TRIGGERS.find((x) => x.value === t)?.label ?? t;
}

function DefaultsPage() {
  const { slug } = Route.useParams();
  const isAdmin = useIsProjectAdmin(slug);
  const { data: defaults, isLoading, isFetching } =
    useDefaultSubscriptions(slug);
  const { data: channels } = useChannels(slug);
  const deleteDefault = useDeleteDefaultSubscription(slug);
  const [dialogOpen, setDialogOpen] = useState(false);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const channelMap = new Map((channels ?? []).map((c) => [c.id, c]));

  if (!isAdmin) {
    return (
      <EmptyState
        icon={Lock}
        title="Admin only"
        description="Default subscriptions can only be configured by project admins."
      />
    );
  }

  return (
    <div className="space-y-4">
      {confirmDialog}
      <div className="rounded-md border border-dashed bg-muted/40 px-4 py-3 text-xs text-muted-foreground">
        These templates are copied into each new member&apos;s subscriptions
        when they join. Existing members are not affected.
      </div>

      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">
            Default Subscriptions
            {isFetching && !isLoading && (
              <RefreshCw className="ml-2 inline size-3 animate-spin text-muted-foreground" />
            )}
          </h3>
          <p className="text-xs text-muted-foreground">
            What should new members automatically subscribe to?
          </p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button size="sm">
              <Plus className="size-4" />
              Add default
            </Button>
          </DialogTrigger>
          <DialogContent>
            <CreateDefaultDialog
              slug={slug}
              channels={channels ?? []}
              onCreated={() => setDialogOpen(false)}
            />
          </DialogContent>
        </Dialog>
      </div>

      {isLoading && <Skeleton className="h-32 w-full" />}
      {defaults && defaults.length === 0 && (
        <EmptyState
          icon={BellRing}
          title="No defaults configured"
          description="Add a default so new members automatically receive key notifications."
        />
      )}
      {defaults && defaults.length > 0 && (
        <Card className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Trigger</TableHead>
                <TableHead>In-app</TableHead>
                <TableHead>Project channels</TableHead>
                <TableHead>Cooldown</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {defaults.map((d) => (
                <TableRow key={d.id}>
                  <TableCell>
                    <Badge variant="outline">
                      {triggerLabel(d.trigger)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {d.in_app ? (
                      <Badge variant="success">yes</Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">no</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {d.project_channel_ids.length === 0
                      ? "-"
                      : d.project_channel_ids
                          .map(
                            (id) =>
                              channelMap.get(id)?.name ?? "deleted",
                          )
                          .join(", ")}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {d.cooldown_seconds}s
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${triggerLabel(d.trigger)} default`}
                      className="text-muted-foreground hover:text-destructive"
                      onClick={() =>
                        confirm({
                          title: "Delete default",
                          description: (
                            <>
                              Stop auto-subscribing new members to the{" "}
                              <code>{triggerLabel(d.trigger)}</code> trigger?
                              Existing members are not affected.
                            </>
                          ),
                          confirmLabel: "Delete",
                          onConfirm: () =>
                            deleteDefault.mutate(d.id, {
                              onSuccess: () =>
                                toast.success("Default deleted"),
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
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create default dialog
// ---------------------------------------------------------------------------

function CreateDefaultDialog({
  slug,
  channels,
  onCreated,
}: {
  slug: string;
  channels: NotificationChannel[];
  onCreated: () => void;
}) {
  const createDefault = useCreateDefaultSubscription(slug);
  const [trigger, setTrigger] = useState<TriggerType>("task.failed");
  const [inApp, setInApp] = useState(true);
  const [selectedChannels, setSelectedChannels] = useState<string[]>([]);
  const [cooldown, setCooldown] = useState("300");

  const toggleChannel = (id: string) => {
    setSelectedChannels((cur) =>
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createDefault.mutate(
      {
        trigger,
        in_app: inApp,
        project_channel_ids: selectedChannels,
        cooldown_seconds: parseInt(cooldown, 10) || 0,
      },
      {
        onSuccess: () => {
          toast.success("Default created");
          onCreated();
        },
        onError: (err) => toast.error(`Failed: ${err.message}`),
      },
    );
  };

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>Add Default Subscription</DialogTitle>
      </DialogHeader>
      <div className="mt-4 space-y-4">
        <div className="space-y-2">
          <Label htmlFor="new-default-trigger">Trigger</Label>
          <Select
            value={trigger}
            onValueChange={(v) => setTrigger(v as TriggerType)}
          >
            <SelectTrigger id="new-default-trigger">
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

        <div className="space-y-2" role="group" aria-labelledby="defaults-delivery-heading">
          {/* Section heading for a group of controls; a Label with
              htmlFor would need a single target and we have many,
              so this is the correct ARIA pattern. */}
          <span
            id="defaults-delivery-heading"
            className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
          >
            Delivery
          </span>
          <div className="space-y-2 rounded-md border p-3">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <Checkbox
                checked={inApp}
                onCheckedChange={(c) => setInApp(c === true)}
              />
              <span>In-app (the bell menu)</span>
            </label>

            {channels.length > 0 && (
              <div className="space-y-1.5 border-t pt-2">
                <p className="text-xs font-medium text-muted-foreground">
                  Project channels
                </p>
                {channels.map((ch) => (
                  <label
                    key={ch.id}
                    className="flex items-center gap-2 text-sm cursor-pointer"
                  >
                    <Checkbox
                      checked={selectedChannels.includes(ch.id)}
                      onCheckedChange={() => toggleChannel(ch.id)}
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
          <Label htmlFor="new-default-cooldown">Cooldown (seconds)</Label>
          <Input
            id="new-default-cooldown"
            type="number"
            min={0}
            max={86400}
            value={cooldown}
            onChange={(e) => setCooldown(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter className="mt-6">
        <Button type="submit" disabled={createDefault.isPending}>
          {createDefault.isPending ? "Creating..." : "Create Default"}
        </Button>
      </DialogFooter>
    </form>
  );
}
