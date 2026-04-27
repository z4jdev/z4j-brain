/**
 * Permanent redirect to the unified Global Notifications hub
 * (v1.0.18). The page itself moved to
 * ``components/notifications/my-channels-tab.tsx`` and is rendered
 * as the ``?tab=channels`` panel of the new hub. Old bookmarks
 * keep working forever.
 */
import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/_authenticated/settings/channels")({
  beforeLoad: () => {
    throw redirect({
      to: "/settings/notifications",
      search: { tab: "channels" },
      replace: true,
    });
  },
});
