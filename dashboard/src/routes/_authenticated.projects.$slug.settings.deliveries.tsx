/**
 * Permanent redirect to the unified Project Notifications hub
 * (v1.0.18). The page itself moved to
 * ``components/notifications/project-deliveries-tab.tsx`` and is
 * rendered as the ``?tab=deliveries`` panel of the new hub.
 */
import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute(
  "/_authenticated/projects/$slug/settings/deliveries",
)({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/projects/$slug/settings/notifications",
      params: { slug: params.slug },
      search: { tab: "deliveries" },
      replace: true,
    });
  },
});
