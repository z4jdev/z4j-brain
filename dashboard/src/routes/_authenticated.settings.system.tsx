/**
 * Global settings - System status section (admin-only).
 *
 * Runtime info for the brain server, database health, installed
 * packages. This is global, not project-scoped: nothing here
 * depends on which project is active.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableRow,
} from "@/components/ui/table";

export const Route = createFileRoute("/_authenticated/settings/system")({
  component: SystemPage,
});

interface SystemInfo {
  z4j_version: string;
  python_version: string;
  python_implementation: string;
  os: string;
  architecture: string;
  pid: number;
  database_type: string;
  database_version?: string;
  database_size_mb?: number;
  database_connections?: number;
  packages?: Record<string, string>;
}

function SystemPage() {
  const { data, isLoading } = useQuery<SystemInfo>({
    queryKey: ["system-info"],
    queryFn: () => api.get<SystemInfo>("/health/system"),
    staleTime: 60_000,
  });

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (!data) return null;

  return (
    <div className="space-y-6">
      <Card className="p-6">
        <h3 className="text-sm font-semibold">Brain Server</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Core runtime information for the z4j brain process.
        </p>
        <div className="mt-4">
          <StatusTable
            rows={[
              ["z4j version", data.z4j_version],
              ["Python", `${data.python_version} (${data.python_implementation})`],
              ["OS", data.os],
              ["Architecture", data.architecture],
              ["PID", String(data.pid)],
            ]}
          />
        </div>
      </Card>

      <Card className="p-6">
        <h3 className="text-sm font-semibold">Database</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Connected database information and health.
        </p>
        <div className="mt-4">
          <StatusTable
            rows={[
              ["Type", data.database_type],
              ...(data.database_version
                ? [["Version", data.database_version] as [string, string]]
                : []),
              ...(data.database_size_mb !== undefined
                ? [["Size", `${data.database_size_mb} MB`] as [string, string]]
                : []),
              ...(data.database_connections !== undefined
                ? [["Active connections", String(data.database_connections)] as [string, string]]
                : []),
            ]}
          />
        </div>
      </Card>

      {data.packages && Object.keys(data.packages).length > 0 && (
        <Card className="p-6">
          <h3 className="text-sm font-semibold">Installed Packages</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Key Python package versions in the brain environment.
          </p>
          <div className="mt-4">
            <StatusTable
              rows={Object.entries(data.packages)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([name, version]) => [name, version])}
            />
          </div>
        </Card>
      )}
    </div>
  );
}

function StatusTable({ rows }: { rows: [string, string][] }) {
  return (
    <Table>
      <TableBody>
        {rows.map(([key, value]) => (
          <TableRow key={key}>
            <TableCell className="w-1/3 py-2 font-medium text-muted-foreground">
              {key}
            </TableCell>
            <TableCell className="py-2 font-mono text-sm">{value}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
