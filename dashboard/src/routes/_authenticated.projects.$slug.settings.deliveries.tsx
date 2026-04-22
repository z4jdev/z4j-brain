/**
 * Project settings - Delivery Log section.
 *
 * Audit log of every notification sent, failed, or skipped by cooldown.
 */
import { createFileRoute } from "@tanstack/react-router";
import { Lock, RefreshCw, Send } from "lucide-react";
import { EmptyState } from "@/components/domain/empty-state";
import { useIsProjectAdmin } from "@/hooks/use-memberships";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { DateCell } from "@/components/domain/date-cell";
import { useDeliveries } from "@/hooks/use-notifications";

export const Route = createFileRoute(
  "/_authenticated/projects/$slug/settings/deliveries",
)({
  component: DeliveriesPage,
});

function DeliveriesPage() {
  const { slug } = Route.useParams();
  const isAdmin = useIsProjectAdmin(slug);
  // Response envelope is now {items, next_cursor} per POL-2.
  const { data, isLoading, isFetching } = useDeliveries(slug);
  const deliveries = data?.items;

  if (!isAdmin) {
    return (
      <EmptyState
        icon={Lock}
        title="Admin only"
        description="The delivery log is only visible to project admins."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold">
          Delivery History
          {isFetching && !isLoading && (
            <RefreshCw className="ml-2 inline size-3 animate-spin text-muted-foreground" />
          )}
        </h3>
        <p className="text-xs text-muted-foreground">
          Every notification attempt - sent, failed, or skipped by cooldown.
        </p>
      </div>

      {isLoading && <Skeleton className="h-32 w-full" />}
      {deliveries && deliveries.length === 0 && (
        <EmptyState
          icon={Send}
          title="No deliveries yet"
          description="Notifications will appear here once subscriptions start firing."
        />
      )}
      {deliveries && deliveries.length > 0 && (
        <Card className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Trigger</TableHead>
                <TableHead>Task</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Response</TableHead>
                <TableHead className="text-right">Sent</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {deliveries.map((d) => (
                <TableRow key={d.id}>
                  <TableCell>
                    <Badge variant="outline">{d.trigger}</Badge>
                  </TableCell>
                  <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground">
                    {d.task_name ?? d.task_id ?? "-"}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        d.status === "sent"
                          ? "success"
                          : d.status === "skipped"
                            ? "muted"
                            : "destructive"
                      }
                    >
                      {d.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {d.response_code ?? "-"}
                    {d.error && (
                      <span className="ml-1 text-destructive" title={d.error}>
                        ⚠
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <DateCell value={d.sent_at} />
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
