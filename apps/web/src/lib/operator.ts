/** Shared operator UI helpers. */

export type OperatorStatus = {
  optimization_enabled: boolean;
  autonomous_execution_enabled: boolean;
  autonomous_kill_switch: boolean;
  optimization_mode: string;
  autonomy_mode: string;
  automation_enabled: boolean;
  providers: Record<
    string,
    { connected: boolean; credentials_configured: boolean; autonomous_enabled: boolean; status: string }
  >;
  safety: Record<string, unknown>;
  usage: { closed_loop_actions_today: number; max_actions_per_day: number };
  kill_switch: { enabled: boolean; effect: string };
  scheduler_enabled: boolean;
  canary?: {
    enabled: boolean;
    org_allowlisted: boolean;
    actions: string;
    providers: string;
    verification_max_age_hours: number;
    note: string;
  };
};

export type CanaryStatus = {
  canary_enabled: boolean;
  readiness: string;
  environment: string;
  kill_switch: boolean;
  autonomous_execution_enabled: boolean;
  optimization_enabled: boolean;
  allowlists: {
    orgs_configured: boolean;
    providers: string;
    actions: string;
    environments: string;
    meta_ad_accounts_configured: boolean;
    meta_campaigns_configured: boolean;
    google_customers_configured: boolean;
    google_campaigns_configured: boolean;
  };
  limits: {
    max_actions_per_run: number;
    max_actions_per_day: number;
    max_spend_impact: number;
    actions_used_24h: number;
    actions_remaining_24h: number;
    verification_max_age_hours: number;
  };
  providers: Array<{
    provider: string;
    connected: boolean;
    verification_status?: string | null;
    verification_checked_at?: string | null;
    account_hint?: string | null;
  }>;
  eligible_actions: string[];
  preferred_actions: string[];
  confirm_phrase: string;
  notes: string[];
};

export type AmbiguousAction = {
  action_id: string;
  action_type: string;
  platform: string | null;
  provider: string;
  operation: string;
  external_id: string | null;
  status: string;
  reconciliation_state: string;
  ambiguous_error: string | null;
  ambiguous_since: string | null;
  last_checked_at: string | null;
  last_outcome: string | null;
  updated_at: string | null;
};

export type PerformanceRec = {
  id: string;
  client_id: string | null;
  platform: string;
  title: string;
  recommendation_type: string;
  severity: string;
  confidence: number | string;
  status: string;
  explanation: string;
  evidence: unknown[];
  suggested_action: Record<string, unknown>;
  external_campaign_id: string | null;
  signal_category: string | null;
  created_at: string;
  expires_at: string | null;
  current_values?: Record<string, unknown>;
};
