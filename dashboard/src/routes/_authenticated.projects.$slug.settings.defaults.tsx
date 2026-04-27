/**
 * Permanent redirect to the unified Project Notifications hub
 * (v1.0.18). The page itself moved to
 * ``components/notifications/default-subscriptions-tab.tsx`` and
 * is rendered as the ``?tab=defaults`` panel of the new hub.
 */
import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute(
  "/_authenticated/projects/$slug/settings/defaults",
)({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/projects/$slug/settings/notifications",
      params: { slug: params.slug },
      search: { tab: "defaults" },
      replace: true,
    });
  },
});
