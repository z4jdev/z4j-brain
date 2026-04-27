/**
 * Project Notifications hub - "Project Notifications" page (v1.0.18).
 *
 * Combines what used to be three separate routes into one tabbed
 * page so the admin has ONE place to manage everything notification-
 * sending for THIS project:
 *
 *   - Tab "Project Channels"      (was /providers)
 *   - Tab "Default Subscriptions" (was /defaults)
 *   - Tab "Delivery Log"          (was /deliveries)
 *
 * Admin-gated. Members see this entry hidden from the project
 * sidebar; if they URL-jump in directly, the inner tabs render
 * their own admin-only EmptyState.
 *
 * The mirror page on the personal side
 * (``_authenticated.settings.notifications.tsx``) is "Global
 * Notifications" — channels + subscriptions + delivery history,
 * all personal scope.
 */
import { createFileRoute, useSearch } from "@tanstack/react-router";
import { z } from "zod";
import { PageHeader } from "@/components/domain/page-header";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { DefaultSubscriptionsTab } from "@/components/notifications/default-subscriptions-tab";
import { ProjectChannelsTab } from "@/components/notifications/project-channels-tab";
import { ProjectDeliveriesTab } from "@/components/notifications/project-deliveries-tab";

const TABS = ["channels", "defaults", "deliveries"] as const;
type TabValue = (typeof TABS)[number];

const searchSchema = z.object({
  tab: z.enum(TABS).optional(),
});

export const Route = createFileRoute(
  "/_authenticated/projects/$slug/settings/notifications",
)({
  validateSearch: searchSchema,
  component: ProjectNotificationsPage,
});

function ProjectNotificationsPage() {
  const { slug } = Route.useParams();
  const search = useSearch({ from: Route.id });
  const navigate = Route.useNavigate();
  const activeTab: TabValue = search.tab ?? "channels";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Project Notifications"
        description="What this project announces, through which channels, and to whom by default. Members can wire their own personal subscriptions in Global Notifications under their account."
      />
      <Tabs
        value={activeTab}
        onValueChange={(v) =>
          navigate({
            search: { tab: v as TabValue },
            replace: true,
          })
        }
      >
        <TabsList>
          <TabsTrigger value="channels">Project Channels</TabsTrigger>
          <TabsTrigger value="defaults">Default Subscriptions</TabsTrigger>
          <TabsTrigger value="deliveries">Delivery Log</TabsTrigger>
        </TabsList>
        <TabsContent value="channels" className="mt-4">
          <ProjectChannelsTab slug={slug} />
        </TabsContent>
        <TabsContent value="defaults" className="mt-4">
          <DefaultSubscriptionsTab slug={slug} />
        </TabsContent>
        <TabsContent value="deliveries" className="mt-4">
          <ProjectDeliveriesTab slug={slug} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
