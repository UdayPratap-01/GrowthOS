"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { api } from "@/lib/api";
import { AutonomySettings, User } from "@/types";

export default function SettingsPage() {
  const [user, setUser] = useState<User | null>(null);
  const [settings, setSettings] = useState<AutonomySettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setUser(await api<User>("/auth/me"));
      setSettings(await api<AutonomySettings>("/autopilot/settings"));
    })();
  }, []);

  async function save() {
    if (!settings) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api<AutonomySettings>("/autopilot/settings", {
        method: "PUT",
        body: JSON.stringify({
          autonomy_mode: settings.autonomy_mode,
          automation_enabled: settings.automation_enabled,
          maximum_daily_ad_spend: Number(settings.maximum_daily_ad_spend),
          maximum_campaign_budget: Number(settings.maximum_campaign_budget),
          maximum_budget_increase_percentage: Number(settings.maximum_budget_increase_percentage),
          maximum_budget_decrease_percentage: Number(settings.maximum_budget_decrease_percentage),
          maximum_campaigns_per_day: Number(settings.maximum_campaigns_per_day),
          maximum_creatives_per_day: Number(settings.maximum_creatives_per_day),
          maximum_posts_per_day: Number(settings.maximum_posts_per_day),
          require_approval_for_financial_actions: settings.require_approval_for_financial_actions,
          require_approval_for_publishing: settings.require_approval_for_publishing,
          require_approval_for_campaign_creation: settings.require_approval_for_campaign_creation,
          allowed_platforms: settings.allowed_platforms,
          allowed_actions: settings.allowed_actions,
        }),
      });
      setSettings(updated);
      setNotice("Autonomy settings saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  if (!user || !settings) return <Skeleton className="h-48 w-full" />;

  return (
    <div className="space-y-6 animate-rise">
      <div>
        <h1 className="font-display text-3xl">Settings</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">Organization preferences and Autopilot safety limits.</p>
      </div>

      {notice ? <Card><p className="text-sm text-[var(--accent-ink)]">{notice}</p></Card> : null}
      {error ? <EmptyState title="Settings error" description={error} /> : null}

      <Card>
        <CardHeader
          title="Organization"
          action={
            <Badge tone={user.operating_mode === "LIVE" ? "success" : "demo"}>
              {user.operating_mode || (user.demo_mode ? "DEMO" : "LIVE")} mode
            </Badge>
          }
        />
        <dl className="grid gap-3 text-sm md:grid-cols-2">
          <div><dt className="text-[var(--muted)]">Name</dt><dd>{user.organization_name}</dd></div>
          <div><dt className="text-[var(--muted)]">Role</dt><dd className="capitalize">{user.role}</dd></div>
          <div><dt className="text-[var(--muted)]">User</dt><dd>{user.full_name}</dd></div>
          <div><dt className="text-[var(--muted)]">Email</dt><dd>{user.email}</dd></div>
        </dl>
        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-[var(--line)] pt-4">
          <p className="flex-1 text-sm text-[var(--muted)]">
            Org demo flag: {user.organization_demo_mode ? "on" : "off"}. Env DEMO_MODE: {user.env_demo_mode ? "on" : "off"}.
            Effective mode is DEMO if either is on. Live mode never silently invents metrics.
          </p>
          <Button
            size="sm"
            variant="secondary"
            onClick={async () => {
              try {
                const next = await api<User>("/auth/organization/mode", {
                  method: "PATCH",
                  body: JSON.stringify({ demo_mode: !Boolean(user.organization_demo_mode) }),
                });
                setUser(next);
                setNotice(`Organization demo flag → ${next.organization_demo_mode ? "on" : "off"} (effective ${next.operating_mode})`);
              } catch (e2) {
                setError(e2 instanceof Error ? e2.message : "Mode update failed");
              }
            }}
          >
            Toggle org demo flag
          </Button>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Autonomy & automation"
          subtitle="Copilot recommends. Assisted auto-runs low risk. Autonomous stays inside hard limits."
          action={<Button disabled={saving} onClick={save}>{saving ? "Saving..." : "Save"}</Button>}
        />
        <div className="grid gap-4 md:grid-cols-2">
          <label className="text-sm">
            <span className="text-[var(--muted)]">Autonomy mode</span>
            <Select
              className="mt-1"
              value={settings.autonomy_mode}
              onChange={(e) => setSettings({ ...settings, autonomy_mode: e.target.value as AutonomySettings["autonomy_mode"] })}
            >
              <option value="copilot">Copilot</option>
              <option value="assisted">Assisted Autopilot</option>
              <option value="autonomous">Autonomous</option>
            </Select>
          </label>
          <label className="flex items-end gap-2 text-sm">
            <input
              type="checkbox"
              checked={settings.automation_enabled}
              onChange={(e) => setSettings({ ...settings, automation_enabled: e.target.checked })}
            />
            Automation enabled
          </label>
          <label className="text-sm">
            <span className="text-[var(--muted)]">Max daily ad spend</span>
            <Input
              className="mt-1"
              type="number"
              value={Number(settings.maximum_daily_ad_spend)}
              onChange={(e) => setSettings({ ...settings, maximum_daily_ad_spend: e.target.value })}
            />
          </label>
          <label className="text-sm">
            <span className="text-[var(--muted)]">Max campaign budget</span>
            <Input
              className="mt-1"
              type="number"
              value={Number(settings.maximum_campaign_budget)}
              onChange={(e) => setSettings({ ...settings, maximum_campaign_budget: e.target.value })}
            />
          </label>
          <label className="text-sm">
            <span className="text-[var(--muted)]">Max budget increase %</span>
            <Input
              className="mt-1"
              type="number"
              value={Number(settings.maximum_budget_increase_percentage)}
              onChange={(e) => setSettings({ ...settings, maximum_budget_increase_percentage: e.target.value })}
            />
          </label>
          <label className="text-sm">
            <span className="text-[var(--muted)]">Max budget decrease %</span>
            <Input
              className="mt-1"
              type="number"
              value={Number(settings.maximum_budget_decrease_percentage)}
              onChange={(e) => setSettings({ ...settings, maximum_budget_decrease_percentage: e.target.value })}
            />
          </label>
          <label className="text-sm">
            <span className="text-[var(--muted)]">Max campaigns / day</span>
            <Input
              className="mt-1"
              type="number"
              value={settings.maximum_campaigns_per_day}
              onChange={(e) => setSettings({ ...settings, maximum_campaigns_per_day: Number(e.target.value) })}
            />
          </label>
          <label className="text-sm">
            <span className="text-[var(--muted)]">Max creatives / day</span>
            <Input
              className="mt-1"
              type="number"
              value={settings.maximum_creatives_per_day}
              onChange={(e) => setSettings({ ...settings, maximum_creatives_per_day: Number(e.target.value) })}
            />
          </label>
          <label className="text-sm">
            <span className="text-[var(--muted)]">Max posts / day</span>
            <Input
              className="mt-1"
              type="number"
              value={settings.maximum_posts_per_day}
              onChange={(e) => setSettings({ ...settings, maximum_posts_per_day: Number(e.target.value) })}
            />
          </label>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <input
              type="checkbox"
              checked={settings.require_approval_for_financial_actions}
              onChange={(e) => setSettings({ ...settings, require_approval_for_financial_actions: e.target.checked })}
            />
            Require approval for financial actions
          </label>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <input
              type="checkbox"
              checked={settings.require_approval_for_publishing}
              onChange={(e) => setSettings({ ...settings, require_approval_for_publishing: e.target.checked })}
            />
            Require approval for publishing
          </label>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <input
              type="checkbox"
              checked={settings.require_approval_for_campaign_creation}
              onChange={(e) => setSettings({ ...settings, require_approval_for_campaign_creation: e.target.checked })}
            />
            Require approval for campaign creation
          </label>
        </div>
      </Card>
    </div>
  );
}
