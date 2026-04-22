import { useState } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Activity, Eye, EyeOff, Loader2 } from "lucide-react";
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

export const Route = createFileRoute("/login")({
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
    <div className="grid min-h-screen w-full place-items-center bg-background p-6">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex items-center justify-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-lg shadow-primary/20">
            <Activity className="size-5" />
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

        <p className="text-center text-xs text-muted-foreground">
          first time here?{" "}
          <a
            href="/setup"
            className="text-primary underline-offset-4 hover:underline"
          >
            run the first-boot setup
          </a>
        </p>
      </div>
    </div>
  );
}
