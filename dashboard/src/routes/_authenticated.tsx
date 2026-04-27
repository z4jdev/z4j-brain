import { useEffect } from "react";
import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { api, ApiError } from "@/lib/api";
import type { UserMePublic } from "@/lib/api-types";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { SidebarProvider } from "@/components/layout/sidebar-context";
import { Topbar } from "@/components/layout/topbar";
import {
  CommandPalette,
  useCommandPalette,
} from "@/components/command-palette";
import { ShortcutsDialog } from "@/components/shortcuts-dialog";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";

export const Route = createFileRoute("/_authenticated")({
  beforeLoad: async () => {
    try {
      const me = await api.get<UserMePublic>("/auth/me");
      return { user: me };
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        throw redirect({ to: "/login" });
      }
      throw err;
    }
  },
  component: AuthenticatedLayout,
});

function AuthenticatedLayout() {
  const palette = useCommandPalette();
  const shortcuts = useKeyboardShortcuts();

  // Apply saved primary color on mount.
  useEffect(() => {
    const hue = localStorage.getItem("z4j-primary-hue");
    if (hue) {
      const h = parseInt(hue, 10);
      const root = document.documentElement;
      root.style.setProperty("--primary", `oklch(0.55 0.18 ${h})`);
      root.style.setProperty("--primary-foreground", `oklch(0.99 0.005 ${h})`);
      root.style.setProperty("--ring", `oklch(0.55 0.18 ${h})`);
      root.style.setProperty("--sidebar-primary", `oklch(0.55 0.18 ${h})`);
      root.style.setProperty("--sidebar-primary-foreground", `oklch(0.99 0.005 ${h})`);
    }
  }, []);

  return (
    <SidebarProvider>
      <div className="flex min-h-screen w-full bg-background">
        <AppSidebar />
        <main className="flex min-w-0 flex-1 flex-col">
          <Topbar />
          <Outlet />
        </main>
      </div>

      {/* Global overlays */}
      <CommandPalette open={palette.open} onOpenChange={palette.setOpen} />
      <ShortcutsDialog
        open={shortcuts.helpOpen}
        onOpenChange={shortcuts.setHelpOpen}
      />
    </SidebarProvider>
  );
}
