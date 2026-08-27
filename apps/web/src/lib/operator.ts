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
