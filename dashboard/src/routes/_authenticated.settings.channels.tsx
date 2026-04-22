/**
 * User settings - personal notification channels.
 *
 * CRUD for the user's own delivery destinations. These can be attached
 * to any subscription the user creates across all their projects.
 */
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import {
  Globe,
  Mail,
  MessageSquare,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  Webhook,
} from "lucide-react";
import { toast } from "sonner";
import { useConfirm } from "@/components/domain/confirm-dialog";
import { EmptyState } from "@/components/domain/empty-state";
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
import {
  useCreateUserChannel,
  useDeleteUserChannel,
  useUserChannels,
  type ChannelType,
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

function UserChannelsPage() {
  const { data: channels, isLoading, isFetching } = useUserChannels();
  const deleteChannel = useDeleteUserChannel();
  const [dialogOpen, setDialogOpen] = useState(false);
  const { confirm, dialog: confirmDialog } = useConfirm();

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
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button size="sm">
                <Plus className="size-4" />
                Add Channel
              </Button>
            </DialogTrigger>
            <DialogContent>
              <CreateUserChannelDialog onCreated={() => setDialogOpen(false)} />
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
            return (
              <Card key={ch.id} className="flex items-start gap-3 p-4">
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
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Delete ${ch.name}`}
                  className="shrink-0 text-muted-foreground hover:text-destructive"
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
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create user channel dialog
// ---------------------------------------------------------------------------

function CreateUserChannelDialog({ onCreated }: { onCreated: () => void }) {
  const createChannel = useCreateUserChannel();
  const [type, setType] = useState<ChannelType>("webhook");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPass, setSmtpPass] = useState("");
  const [fromAddr, setFromAddr] = useState("");
  const [toAddrs, setToAddrs] = useState("");
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");

  const buildConfig = (): Record<string, unknown> => {
    switch (type) {
      case "webhook":
        return { url };
      case "email":
        return {
          smtp_host: smtpHost,
          smtp_port: parseInt(smtpPort, 10),
          smtp_user: smtpUser,
          smtp_pass: smtpPass,
          smtp_tls: true,
          from_addr: fromAddr || smtpUser,
          to_addrs: toAddrs.split(",").map((s) => s.trim()).filter(Boolean),
        };
      case "slack":
        return { webhook_url: url };
      case "telegram":
        return { bot_token: botToken, chat_id: chatId };
      default:
        return {};
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createChannel.mutate(
      { name, type, config: buildConfig() },
      {
        onSuccess: () => {
          toast.success("Channel created");
          onCreated();
        },
        onError: (err) => toast.error(`Failed: ${err.message}`),
      },
    );
  };

  return (
    <form onSubmit={handleSubmit}>
      <DialogHeader>
        <DialogTitle>Add Personal Channel</DialogTitle>
      </DialogHeader>
      <div className="mt-4 space-y-4">
        <div className="space-y-2">
          <Label htmlFor="new-channel-type">Channel type</Label>
          <Select value={type} onValueChange={(v) => setType(v as ChannelType)}>
            <SelectTrigger id="new-channel-type">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="webhook">Webhook</SelectItem>
              <SelectItem value="email">Email (SMTP)</SelectItem>
              <SelectItem value="slack">Slack</SelectItem>
              <SelectItem value="telegram">Telegram</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="new-channel-name">Name</Label>
          <Input
            id="new-channel-name"
            placeholder="e.g. My phone, personal email"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>

        {type === "webhook" && (
          <div className="space-y-2">
            <Label htmlFor="new-channel-webhook-url">Webhook URL</Label>
            <Input
              id="new-channel-webhook-url"
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
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="new-channel-smtp-host">SMTP Host</Label>
                <Input
                  id="new-channel-smtp-host"
                  placeholder="smtp.gmail.com"
                  value={smtpHost}
                  onChange={(e) => setSmtpHost(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-channel-smtp-port">Port</Label>
                <Input
                  id="new-channel-smtp-port"
                  type="number"
                  value={smtpPort}
                  onChange={(e) => setSmtpPort(e.target.value)}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="new-channel-smtp-user">Username</Label>
                <Input
                  id="new-channel-smtp-user"
                  value={smtpUser}
                  onChange={(e) => setSmtpUser(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="new-channel-smtp-pass">Password</Label>
                <Input
                  id="new-channel-smtp-pass"
                  type="password"
                  value={smtpPass}
                  onChange={(e) => setSmtpPass(e.target.value)}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-channel-from">From address</Label>
              <Input
                id="new-channel-from"
                type="email"
                placeholder="alerts@example.com"
                value={fromAddr}
                onChange={(e) => setFromAddr(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-channel-to">
                To addresses (comma-separated)
              </Label>
              <Input
                id="new-channel-to"
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
            <Label htmlFor="new-channel-slack-url">Slack Webhook URL</Label>
            <Input
              id="new-channel-slack-url"
              type="url"
              pattern="https://hooks\.slack\.com/.*"
              placeholder="https://hooks.slack.com/services/..."
              title="Must be a Slack webhook URL starting with https://hooks.slack.com/"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
            />
          </div>
        )}

        {type === "telegram" && (
          <>
            <div className="space-y-2">
              <Label htmlFor="new-channel-bot-token">Bot Token</Label>
              <Input
                id="new-channel-bot-token"
                placeholder="123456:ABC-DEF..."
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-channel-chat-id">Chat ID</Label>
              <Input
                id="new-channel-chat-id"
                placeholder="-100123456789"
                value={chatId}
                onChange={(e) => setChatId(e.target.value)}
                required
              />
            </div>
          </>
        )}
      </div>
      <DialogFooter className="mt-6">
        <Button type="submit" disabled={createChannel.isPending}>
          {createChannel.isPending ? "Creating..." : "Create Channel"}
        </Button>
      </DialogFooter>
    </form>
  );
}
