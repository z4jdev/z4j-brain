/**
 * User settings - personal notification channels.
 *
 * CRUD for the user's own delivery destinations. These can be attached
 * to any subscription the user creates across all their projects.
 *
 * Feature parity with the project-level Providers page
 * (``_authenticated.projects.$slug.settings.providers.tsx``): create,
 * edit, test, delete + SMTP quick presets + Gmail app-password hint.
 * The two pages share the same test-payload + result shape so the
 * dashboard toast renderer works against either.
 */
import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  CheckCircle2,
  Globe,
  Mail,
  MessageSquare,
  Pencil,
  Plus,
  RefreshCw,
  Send,
  TestTube,
  Trash2,
  Webhook,
  X,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { useConfirm } from "@/components/domain/confirm-dialog";
import { EmptyState } from "@/components/domain/empty-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/domain/page-header";
import { api } from "@/lib/api";
import {
  useCreateUserChannel,
  useDeleteUserChannel,
  useTestUserChannel,
  useTestUserChannelConfig,
  useUpdateUserChannel,
  useUserChannels,
  type ChannelTestResult,
  type ChannelType,
  type UserChannel,
} from "@/hooks/use-notifications";

export const Route = createFileRoute("/_authenticated/settings/channels")({
  component: UserChannelsPage,
});

const CHANNEL_ICONS = {
  webhook: Webhook,
  email: Mail,
  slack: MessageSquare,
  telegram: Send,
} as const;

const MASK = "••••••••";

function UserChannelsPage() {
  const { data: channels, isLoading, isFetching } = useUserChannels();
  const deleteChannel = useDeleteUserChannel();
  const testChannel = useTestUserChannel();
  const [dialogState, setDialogState] = useState<
    | { mode: "closed" }
    | { mode: "create" }
    | { mode: "edit"; channel: UserChannel }
  >({ mode: "closed" });
  // Per-card test result so the Test button produces visible
  // feedback even if the corner toast is missed. See providers.tsx
  // for the rationale - mirrored here for parity. Persistent,
  // dismissed manually via the × button.
  const [testResults, setTestResults] = useState<
    Record<string, ChannelTestResult>
  >({});
  const { confirm, dialog: confirmDialog } = useConfirm();

  const closeDialog = () => setDialogState({ mode: "closed" });

  const recordTestResult = (id: string, res: ChannelTestResult) => {
    setTestResults((prev) => ({ ...prev, [id]: res }));
  };

  const dismissTestResult = (id: string) => {
    setTestResults((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  return (
    <div className="space-y-6">
      {confirmDialog}
      <PageHeader
        title={
          <>
            My Channels
            {isFetching && !isLoading && (
              <RefreshCw className="ml-2 inline size-3 animate-spin text-muted-foreground" />
            )}
          </>
        }
        description="Your personal delivery destinations - webhook, email, Slack, or Telegram. Attach these to any subscription across all your projects."
        actions={
          <Dialog
            open={dialogState.mode !== "closed"}
            onOpenChange={(open) => {
              if (!open) closeDialog();
            }}
          >
            <DialogTrigger asChild>
              <Button
                size="sm"
                onClick={() => setDialogState({ mode: "create" })}
              >
                <Plus className="size-4" />
                Add Channel
              </Button>
            </DialogTrigger>
            <DialogContent>
              {dialogState.mode !== "closed" && (
                <UserChannelDialog
                  mode={dialogState.mode}
                  channel={
                    dialogState.mode === "edit" ? dialogState.channel : undefined
                  }
                  onClose={closeDialog}
                />
              )}
            </DialogContent>
          </Dialog>
        }
      />

      {isLoading && <Skeleton className="h-32 w-full" />}
      {channels && channels.length === 0 && (
        <EmptyState
          icon={Globe}
          title="No channels yet"
          description="Add a personal webhook, email, Slack, or Telegram destination."
        />
      )}
      {channels && channels.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {channels.map((ch) => {
            const Icon = CHANNEL_ICONS[ch.type] ?? Globe;
            const testingThis =
              testChannel.isPending && testChannel.variables === ch.id;
            const testResult = testResults[ch.id];
            return (
              <Card key={ch.id} className="flex flex-col gap-3 p-4">
                <div className="flex items-start gap-3">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <Icon className="size-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">{ch.name}</span>
                    <Badge variant={ch.is_active ? "success" : "muted"}>
                      {ch.is_active ? "active" : "disabled"}
                    </Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {ch.type}
                    {ch.type === "webhook" &&
                      typeof ch.config.url === "string" &&
                      ` · ${ch.config.url.slice(0, 40)}...`}
                    {ch.type === "email" &&
                      typeof ch.config.smtp_host === "string" &&
                      ` · ${ch.config.smtp_host}`}
                    {ch.type === "slack" &&
                      typeof ch.config.webhook_url === "string" &&
                      ` · hooks.slack.com`}
                    {ch.type === "telegram" &&
                      typeof ch.config.chat_id === "string" &&
                      ` · chat ${ch.config.chat_id}`}
                  </p>
                </div>
                <div className="flex shrink-0 items-center">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Test ${ch.name}`}
                    title="Send a test notification"
                    disabled={testingThis}
                    className="text-muted-foreground hover:text-primary"
                    onClick={() =>
                      testChannel.mutate(ch.id, {
                        // Card-level test: inline banner only, no
                        // toast. See providers.tsx for rationale -
                        // the card anchors the result, so a
                        // duplicate toast was pure visual noise.
                        onSuccess: (res) => recordTestResult(ch.id, res),
                        onError: (err) =>
                          recordTestResult(ch.id, {
                            success: false,
                            status_code: null,
                            response_body: null,
                            error:
                              err instanceof Error
                                ? err.message
                                : "Test failed",
                          }),
                      })
                    }
                  >
                    {testingThis ? (
                      <RefreshCw className="size-4 animate-spin" />
                    ) : (
                      <TestTube className="size-4" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Edit ${ch.name}`}
                    title="Edit channel"
                    className="text-muted-foreground hover:text-primary"
                    onClick={() =>
                      setDialogState({ mode: "edit", channel: ch })
                    }
                  >
                    <Pencil className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Delete ${ch.name}`}
                    title="Delete channel"
                    className="text-muted-foreground hover:text-destructive"
                    onClick={() =>
                      confirm({
                        title: "Delete channel",
                        description: (
                          <>
                            This removes <code>{ch.name}</code> and any
                            subscriptions pointing to it.
                          </>
                        ),
                        confirmLabel: "Delete",
                        onConfirm: () =>
                          deleteChannel.mutate(ch.id, {
                            onSuccess: () =>
                              toast.success("Channel deleted"),
                            onError: (err) => {
                              const msg =
                                err instanceof Error
                                  ? err.message
                                  : "Request failed";
                              toast.error(msg);
                            },
                          }),
                      })
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
                </div>
                {testResult && (
                  <div
                    role="status"
                    aria-live="polite"
                    className={
                      testResult.success
                        ? "rounded-md border border-success/40 bg-success/10 px-3 py-2 text-xs text-success-foreground"
                        : "rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
                    }
                  >
                    <div className="flex items-start gap-2">
                      {testResult.success ? (
                        <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />
                      ) : (
                        <XCircle className="mt-0.5 size-3.5 shrink-0" />
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="font-medium">
                          {testResult.success
                            ? testResult.status_code
                              ? `Test sent (HTTP ${testResult.status_code})`
                              : "Test sent"
                            : "Test failed"}
                        </p>
                        {!testResult.success && testResult.error && (
                          <p className="mt-0.5 break-words opacity-90">
                            {testResult.error}
                          </p>
                        )}
                      </div>
                      <button
                        type="button"
                        aria-label="Dismiss test result"
                        onClick={() => dismissTestResult(ch.id)}
                        className="shrink-0 rounded p-0.5 opacity-70 transition-opacity hover:opacity-100 focus:opacity-100 focus:outline-none focus:ring-1 focus:ring-current"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// User channel dialog - handles create AND edit
// ---------------------------------------------------------------------------

interface UserChannelDialogProps {
  mode: "create" | "edit";
  channel?: UserChannel;
  onClose: () => void;
}

function UserChannelDialog({ mode, channel, onClose }: UserChannelDialogProps) {
  const createChannel = useCreateUserChannel();
  const updateChannel = useUpdateUserChannel();
  const testConfig = useTestUserChannelConfig();

  // Test-result alert rendered inline inside the dialog body.
  // Sonner toasts render behind the dimmed Dialog overlay from the
  // user's point of view - inline Alert puts feedback where they're
  // looking.
  const [testResult, setTestResult] = useState<ChannelTestResult | null>(null);
  const [testingSaved, setTestingSaved] = useState(false);

  const initial = extractFormFields(mode === "edit" ? channel : undefined);

  const [type, setType] = useState<ChannelType>(
    mode === "edit" && channel ? channel.type : "webhook",
  );
  const [name, setName] = useState(initial.name);
  const [url, setUrl] = useState(initial.url);
  const [smtpHost, setSmtpHost] = useState(initial.smtpHost);
  const [smtpPort, setSmtpPort] = useState(initial.smtpPort);
  const [smtpUser, setSmtpUser] = useState(initial.smtpUser);
  const [smtpPass, setSmtpPass] = useState(initial.smtpPass);
  const [fromAddr, setFromAddr] = useState(initial.fromAddr);
  const [toAddrs, setToAddrs] = useState(initial.toAddrs);
  const [slackUrl, setSlackUrl] = useState(initial.slackUrl);
  const [botToken, setBotToken] = useState(initial.botToken);
  const [chatId, setChatId] = useState(initial.chatId);

  // Clear type-specific state when the user switches channel type in
  // create mode so an abandoned-webhook URL doesn't leak into the
  // Slack field.
  useEffect(() => {
    if (mode === "edit") return;
    setUrl("");
    setSmtpHost("");
    setSmtpPort("587");
    setSmtpUser("");
    setSmtpPass("");
    setFromAddr("");
    setToAddrs("");
    setSlackUrl("");
    setBotToken("");
    setChatId("");
  }, [type, mode]);

  /**
   * Build the config dict for create (full) or edit (merge-friendly).
   *
   * In edit mode, secret fields that the user left blank are
   * OMITTED from the payload. Backend's ``_safe_merge_config``
   * preserves the stored value when the key is missing or equal to
   * the mask. Sending an empty string overwrites - bug we don't
   * reintroduce.
   */
  const buildConfig = (): Record<string, unknown> => {
    const editing = mode === "edit";
    const keepIfBlank = (v: string): string | undefined =>
      editing && !v ? undefined : v;

    switch (type) {
      case "webhook":
        return { url };
      case "email": {
        const cfg: Record<string, unknown> = {
          smtp_host: smtpHost,
          smtp_port: parseInt(smtpPort, 10),
          smtp_user: smtpUser,
          smtp_tls: true,
          from_addr: fromAddr || smtpUser,
          to_addrs: toAddrs.split(",").map((s) => s.trim()).filter(Boolean),
        };
        const pass = keepIfBlank(smtpPass);
        if (pass !== undefined) cfg.smtp_pass = pass;
        return cfg;
      }
      case "slack":
        return { webhook_url: slackUrl };
      case "telegram": {
        const cfg: Record<string, unknown> = { chat_id: chatId };
        const tok = keepIfBlank(botToken);
        if (tok !== undefined) cfg.bot_token = tok;
        return cfg;
      }
      default:
        return {};
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const config = buildConfig();
    if (mode === "edit" && channel) {
      updateChannel.mutate(
        { id: channel.id, body: { name, config } },
        {
          onSuccess: () => {
            toast.success("Channel updated");
            onClose();
          },
          onError: (err) => toast.error(`Failed: ${err.message}`),
        },
      );
    } else {
      createChannel.mutate(
        { name, type, config },
        {
          onSuccess: () => {
            toast.success("Channel created");
            onClose();
          },
          onError: (err) => toast.error(`Failed: ${err.message}`),
        },
      );
    }
  };

  const handleTest = () => {
    // Reset so the new test's outcome replaces any stale alert.
    setTestResult(null);

    if (mode === "edit" && channel) {
      // Test the saved config - form state is incomplete because
      // secrets are masked in the list response.
      setTestingSaved(true);
      void testSavedUserChannel(channel.id)
        .then((res) => setTestResult(res))
        .catch((err) =>
          setTestResult({
            success: false,
            status_code: null,
            response_body: null,
            error: err instanceof Error ? err.message : "Test failed",
          }),
        )
        .finally(() => setTestingSaved(false));
      return;
    }
    testConfig.mutate(
      { type, config: buildConfig() },
      {
        onSuccess: (res) => setTestResult(res),
        onError: (err) =>
          setTestResult({
            success: false,
            status_code: null,
            response_body: null,
            error: err instanceof Error ? err.message : "Test failed",
          }),
      },
    );
  };

  const submitting = createChannel.isPending || updateChannel.isPending;
  const testing = testConfig.isPending || testingSaved;
  const title =
    mode === "edit" ? "Edit Personal Channel" : "Add Personal Channel";
  const submitLabel = mode === "edit" ? "Save Changes" : "Create Channel";

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
      </DialogHeader>
      <div className="mt-4 space-y-4">
        <div className="space-y-2">
          <Label htmlFor="user-channel-type">Channel type</Label>
          <Select
            value={type}
            onValueChange={(v) => setType(v as ChannelType)}
            disabled={mode === "edit"}
          >
            <SelectTrigger id="user-channel-type">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="webhook">Webhook</SelectItem>
              <SelectItem value="email">Email (SMTP)</SelectItem>
              <SelectItem value="slack">Slack</SelectItem>
              <SelectItem value="telegram">Telegram</SelectItem>
            </SelectContent>
          </Select>
          {mode === "edit" && (
            <p className="text-xs text-muted-foreground">
              Channel type is locked after creation. To switch type, delete
              and re-create.
            </p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="user-channel-name">Name</Label>
          <Input
            id="user-channel-name"
            placeholder="e.g. My phone, personal email"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>

        {type === "webhook" && (
          <div className="space-y-2">
            <Label htmlFor="user-channel-webhook-url">Webhook URL</Label>
            <Input
              id="user-channel-webhook-url"
              type="url"
              pattern="https://.*"
              placeholder="https://..."
              title="Must start with https://"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
            />
          </div>
        )}

        {type === "email" && (
          <>
            {/* SMTP provider presets - one-click host + port fill.
                Identical set to the project-level Providers page so
                users don't have to remember "is Brevo on the other
                page too?". */}
            <div className="space-y-2">
              <Label className="text-xs text-muted-foreground">
                Quick presets
              </Label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSmtpHost("smtp.gmail.com");
                    setSmtpPort("587");
                  }}
                >
                  Gmail / Workspace
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSmtpHost("smtp.mailgun.org");
                    setSmtpPort("587");
                  }}
                >
                  Mailgun
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSmtpHost("smtp-relay.brevo.com");
                    setSmtpPort("587");
                  }}
                >
                  Brevo
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSmtpHost("smtp.sendgrid.net");
                    setSmtpPort("587");
                  }}
                >
                  SendGrid
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSmtpHost("smtp.postmarkapp.com");
                    setSmtpPort("587");
                  }}
                >
                  Postmark
                </Button>
              </div>
              {smtpHost === "smtp.gmail.com" && (
                <p className="text-xs text-muted-foreground">
                  Gmail requires 2-step verification + an{" "}
                  <a
                    href="https://support.google.com/accounts/answer/185833"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="underline hover:no-underline"
                  >
                    app password
                  </a>
                  . Use your full Gmail address as the username and the
                  16-character app password as the SMTP password.
                </p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="user-channel-smtp-host">SMTP Host</Label>
                <Input
                  id="user-channel-smtp-host"
                  placeholder="smtp.gmail.com"
                  value={smtpHost}
                  onChange={(e) => setSmtpHost(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="user-channel-smtp-port">Port</Label>
                <Input
                  id="user-channel-smtp-port"
                  type="number"
                  value={smtpPort}
                  onChange={(e) => setSmtpPort(e.target.value)}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="user-channel-smtp-user">Username</Label>
                <Input
                  id="user-channel-smtp-user"
                  value={smtpUser}
                  onChange={(e) => setSmtpUser(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="user-channel-smtp-pass">Password</Label>
                <Input
                  id="user-channel-smtp-pass"
                  type="password"
                  value={smtpPass}
                  onChange={(e) => setSmtpPass(e.target.value)}
                  placeholder={
                    mode === "edit" ? "Leave blank to keep current" : undefined
                  }
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="user-channel-from">From address</Label>
              <Input
                id="user-channel-from"
                type="email"
                placeholder="alerts@example.com"
                value={fromAddr}
                onChange={(e) => setFromAddr(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="user-channel-to">
                To addresses (comma-separated)
              </Label>
              <Input
                id="user-channel-to"
                placeholder="me@example.com"
                value={toAddrs}
                onChange={(e) => setToAddrs(e.target.value)}
                required
              />
            </div>
          </>
        )}

        {type === "slack" && (
          <div className="space-y-2">
            <Label htmlFor="user-channel-slack-url">Slack Webhook URL</Label>
            <Input
              id="user-channel-slack-url"
              type="url"
              pattern="https://hooks\.slack\.com/.*"
              placeholder="https://hooks.slack.com/services/..."
              title="Must be a Slack webhook URL starting with https://hooks.slack.com/"
              value={slackUrl}
              onChange={(e) => setSlackUrl(e.target.value)}
              required
            />
          </div>
        )}

        {type === "telegram" && (
          <>
            <div className="space-y-2">
              <Label htmlFor="user-channel-bot-token">Bot Token</Label>
              <Input
                id="user-channel-bot-token"
                placeholder={
                  mode === "edit"
                    ? "Leave blank to keep current"
                    : "123456:ABC-DEF..."
                }
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                required={mode === "create"}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="user-channel-chat-id">Chat ID</Label>
              <Input
                id="user-channel-chat-id"
                placeholder="-100123456789"
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
                required
              />
            </div>
          </>
        )}
      </div>
      {testResult && (
        <Alert
          variant={testResult.success ? "success" : "destructive"}
          className="mt-4"
        >
          {testResult.success ? (
            <CheckCircle2 />
          ) : (
            <XCircle />
          )}
          <AlertTitle>
            {testResult.success
              ? testResult.status_code
                ? `Test sent (HTTP ${testResult.status_code})`
                : "Test sent"
              : "Test failed"}
          </AlertTitle>
          <AlertDescription>
            {testResult.success ? (
              <p>
                Check the destination inbox / channel for the z4j test
                message. If it doesn&apos;t arrive, verify spam folder and
                the recipient list before saving.
              </p>
            ) : (
              <>
                <p className="break-words">
                  {testResult.error ?? "Unknown error"}
                </p>
                {testResult.response_body && (
                  <p className="mt-1 font-mono text-xs opacity-80">
                    {testResult.response_body}
                  </p>
                )}
              </>
            )}
          </AlertDescription>
        </Alert>
      )}
      <DialogFooter className="mt-6 flex-row-reverse justify-between sm:flex-row-reverse sm:justify-between">
        <Button type="submit" disabled={submitting}>
          {submitting ? "Saving..." : submitLabel}
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={handleTest}
          disabled={testing || submitting}
        >
          {testing ? (
            <>
              <RefreshCw className="size-4 animate-spin" />
              Testing...
            </>
          ) : (
            <>
              <TestTube className="size-4" />
              Test
            </>
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}

/**
 * One-off call to the saved-user-channel test endpoint.
 *
 * The edit dialog routes here (not ``useTestUserChannelConfig``)
 * because secrets are masked in the list response - the form state
 * is incomplete, and the operator's real question is "does the
 * stored channel still work?".
 */
async function testSavedUserChannel(
  channelId: string,
): Promise<ChannelTestResult> {
  return api.post<ChannelTestResult>(`/user/channels/${channelId}/test`, {});
}

interface FormFields {
  name: string;
  url: string;
  smtpHost: string;
  smtpPort: string;
  smtpUser: string;
  smtpPass: string;
  fromAddr: string;
  toAddrs: string;
  slackUrl: string;
  botToken: string;
  chatId: string;
}

function extractFormFields(ch: UserChannel | undefined): FormFields {
  const empty: FormFields = {
    name: "",
    url: "",
    smtpHost: "",
    smtpPort: "587",
    smtpUser: "",
    smtpPass: "",
    fromAddr: "",
    toAddrs: "",
    slackUrl: "",
    botToken: "",
    chatId: "",
  };
  if (!ch) return empty;
  const cfg = ch.config ?? {};
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  const toAddrsVal = (v: unknown): string =>
    Array.isArray(v) ? v.join(", ") : str(v);
  // Masked secrets come back as MASK. Present as empty so the
  // user can leave the field alone (keeps current) or type a new
  // value (replaces).
  const unmask = (v: unknown): string => {
    const s = str(v);
    return s === MASK ? "" : s;
  };
  return {
    name: ch.name,
    url: str(cfg.url),
    smtpHost: str(cfg.smtp_host),
    smtpPort:
      typeof cfg.smtp_port === "number"
        ? String(cfg.smtp_port)
        : str(cfg.smtp_port) || "587",
    smtpUser: str(cfg.smtp_user),
    smtpPass: unmask(cfg.smtp_pass),
    fromAddr: str(cfg.from_addr),
    toAddrs: toAddrsVal(cfg.to_addrs),
    slackUrl: str(cfg.webhook_url),
    botToken: unmask(cfg.bot_token),
    chatId: str(cfg.chat_id),
  };
}
