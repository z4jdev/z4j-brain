/**
 * Personal Notifications hub - "Global Notifications" page (v1.0.18).
 *
 * Combines what used to be three separate routes into one tabbed
 * page so the user has ONE place to manage everything notification-
 * receiving across all their projects:
 *
 *   - Tab "My Channels"           (was /settings/channels)
 *   - Tab "My Subscriptions"      (was /settings/notifications, now with edit)
 *   - Tab "My Delivery History"   (NEW — personal audit log across projects)
 *
 * Old URLs redirect here with the appropriate ``?tab=`` so old
 * bookmarks survive forever (notifications/channels redirect file
 * also lives in this routes directory).
 *
 * The mirror page on the project side
 * (``_authenticated.projects.$slug.settings.notifications.tsx``) is
 * admin-only and combines Project Channels + Default Subscriptions
 * + Delivery Log under "Project Notifications".
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
import { MyChannelsTab } from "@/components/notifications/my-channels-tab";
import { MyDeliveriesTab } from "@/components/notifications/my-deliveries-tab";
import { MySubscriptionsTab } from "@/components/notifications/my-subscriptions-tab";

const TABS = ["channels", "subscriptions", "deliveries"] as const;
type TabValue = (typeof TABS)[number];

const searchSchema = z.object({
  tab: z.enum(TABS).optional(),
});

export const Route = createFileRoute("/_authenticated/settings/notifications")({
  validateSearch: searchSchema,
  component: GlobalNotificationsPage,
});

function GlobalNotificationsPage() {
  const search = useSearch({ from: Route.id });
  const navigate = Route.useNavigate();
  const activeTab: TabValue = search.tab ?? "subscriptions";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Global Notifications"
        description="Personal notification settings that follow you across every project — your channels, your subscriptions, and the full history of alerts that landed in your inbox."
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
          <TabsTrigger value="subscriptions">My Subscriptions</TabsTrigger>
          <TabsTrigger value="channels">My Channels</TabsTrigger>
          <TabsTrigger value="deliveries">My Delivery History</TabsTrigger>
        </TabsList>
        <TabsContent value="subscriptions" className="mt-4">
          <MySubscriptionsTab />
        </TabsContent>
        <TabsContent value="channels" className="mt-4">
          <MyChannelsTab />
        </TabsContent>
        <TabsContent value="deliveries" className="mt-4">
          <MyDeliveriesTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
