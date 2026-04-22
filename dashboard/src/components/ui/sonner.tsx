/**
 * Toast notifications via Sonner.
 *
 * Mounted once at the app root inside `__root.tsx`. Triggered
 * via `import { toast } from "sonner"` from any component.
 */
import { useTheme } from "@/components/layout/theme-provider";
import { Toaster as SonnerToaster } from "sonner";
import type { ComponentProps } from "react";

type ToasterProps = ComponentProps<typeof SonnerToaster>;

export function Toaster({ ...props }: ToasterProps) {
  const { theme } = useTheme();
  return (
    <SonnerToaster
      theme={theme as ToasterProps["theme"]}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-card group-[.toaster]:text-card-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  );
}
