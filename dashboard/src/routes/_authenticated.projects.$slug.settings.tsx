/**
 * Project settings layout route.
 *
 * Left sidebar navigation (mirrors the global settings layout) with a
 * single "Project" section. Renders an Outlet for the active child
 * settings page (members, providers, defaults, deliveries).
 */
import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import {
  BellRing,
  Globe,
  Send,
  Settings2,
  Users,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/domain/page-header";
import { useIsProjectAdmin } from "@/hooks/use-memberships";

export const Route = createFileRoute(
  "/_authenticated/projects/$slug/settings",
)({
  component: ProjectSettingsLayout,
});

interface SettingsNavItem {
  label: string;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  adminOnly?: boolean;
}

interface SettingsNavSection {
  title: string;
  items: SettingsNavItem[];
}

function ProjectSettingsLayout() {
  const { slug } = Route.useParams();
  const isAdmin = useIsProjectAdmin(slug);

  const sections: SettingsNavSection[] = [
    {
      title: "Project",
      items: [
        {
          label: "Members",
          to: "/projects/$slug/settings/members",
          icon: Users,
        },
        {
          label: "Project Channels",
          to: "/projects/$slug/settings/providers",
          icon: Globe,
        },
        {
          label: "Default Subscriptions",
          to: "/projects/$slug/settings/defaults",
          icon: BellRing,
          adminOnly: true,
        },
        {
          label: "Delivery Log",
          to: "/projects/$slug/settings/deliveries",
          icon: Send,
          adminOnly: true,
        },
      ],
    },
  ];

  const visibleSections: SettingsNavSection[] = sections.map((s) => ({
    ...s,
    items: s.items.filter((i) => !i.adminOnly || isAdmin),
  }));

  return (
    <div className="space-y-6 p-4 md:p-6">
      <PageHeader
        title="Project Settings"
        icon={Settings2}
        description={`configuration for project ${slug}`}
      />

      {/* Mobile navigation (horizontal scroll) */}
      <div className="flex gap-1 overflow-x-auto border-b pb-3 md:hidden">
        {visibleSections
          .flatMap((s) => s.items)
          .map((item) => (
            <Link
              key={item.to}
              to={item.to}
              params={{ slug }}
              className={cn(
                "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )}
              activeProps={{
                className: "bg-accent text-accent-foreground",
              }}
            >
              <item.icon className="size-4" />
              {item.label}
            </Link>
          ))}
      </div>

      <div className="flex gap-8">
        {/* Left sidebar navigation */}
        <nav className="hidden w-[220px] shrink-0 md:block">
          <div className="space-y-6">
            {visibleSections.map((section) => (
              <div key={section.title}>
                <h4 className="mb-2 px-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {section.title}
                </h4>
                <ul className="space-y-0.5">
                  {section.items.map((item) => (
                    <li key={item.to}>
                      <Link
                        to={item.to}
                        params={{ slug }}
                        className={cn(
                          "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm font-medium transition-colors",
                          "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                        )}
                        activeProps={{
                          className:
                            "bg-accent text-accent-foreground",
                        }}
                      >
                        <item.icon className="size-4 shrink-0" />
                        <span>{item.label}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </nav>

        {/* Content area */}
        <div className="min-w-0 flex-1">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
