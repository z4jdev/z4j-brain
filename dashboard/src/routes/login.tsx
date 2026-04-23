import { useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { Z4jMark } from "@/components/z4j-mark";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useLogin } from "@/hooks/use-auth";
import { api, ApiError } from "@/lib/api";
import type { SetupStatusResponse } from "@/lib/api-types";

export const Route = createFileRoute("/login")({
  // First-boot guard: if the brain has no admin yet, the login form
  // would just produce 401 invalid_credentials forever (there's no
  // user to authenticate). Hard-redirect to the inline /setup form
  // served by the brain instead. Mirrors the check on the `/` index
  // route - covers the case where the user navigates directly to
  // /login (bookmark, refresh after a prior session).
  beforeLoad: async () => {
    try {
      const status = await api.get<SetupStatusResponse>("/setup/status");
      if (status.first_boot && typeof window !== "undefined") {
        window.location.href = "/setup";
      }
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
    }
  },
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await login.mutateAsync({ email, password });
      toast.success("welcome back");
      navigate({ to: "/" });
    } catch {
      toast.error("invalid email or password");
    }
  }

  return (
    <div className="relative grid min-h-screen w-full place-items-center bg-background p-6">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <div className="w-full max-w-sm space-y-6">
        <div className="flex items-center justify-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-lg shadow-primary/20">
            <Z4jMark className="size-6" />
          </div>
          <div>
            <h1 className="text-xl font-semibold leading-none">z4j</h1>
            <p className="text-xs text-muted-foreground">control plane</p>
          </div>
        </div>

        <Card className="border-border/60 shadow-xl shadow-black/5">
          <CardHeader className="pb-4">
            <CardTitle>Sign in</CardTitle>
            <CardDescription>
              Use your dashboard credentials to access the control plane.
            </CardDescription>
          </CardHeader>
          <form onSubmit={onSubmit}>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="enter your password"
                    className="pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    {showPassword ? (
                      <EyeOff className="size-4" />
                    ) : (
                      <Eye className="size-4" />
                    )}
                  </button>
                </div>
              </div>
            </CardContent>
            <CardFooter className="pt-2">
              <Button type="submit" className="w-full" disabled={login.isPending}>
                {login.isPending && (
                  <Loader2 className="size-4 animate-spin" />
                )}
                Sign in
              </Button>
            </CardFooter>
          </form>
        </Card>

      </div>
    </div>
  );
}
