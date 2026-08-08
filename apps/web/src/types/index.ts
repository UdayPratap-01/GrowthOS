export type User = {
  id: string;
  email: string;
  full_name: string;
  organization_id: string;
  organization_name: string;
  role: string;
  demo_mode: boolean;
  organization_demo_mode?: boolean;
  operating_mode?: "DEMO" | "LIVE";
  env_demo_mode?: boolean;
};

export type Client = {
  id: string;
  organization_id: string;
  business_name: string;
  industry: string | null;
  website: string | null;
  description: string | null;
  location: string | null;
  target_audience: string | null;
  products_services: string | null;
  marketing_goals: string | null;
  monthly_budget: string | number | null;
  brand_voice: string | null;
  competitors: string[];
  primary_channels: string[];
  kpis: string[];
  status: string;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type Dashboard = {
  kpis: {
    total_clients: number;
    total_leads: number;
    total_ad_spend: string | number;
    estimated_revenue: string | number;
    average_cpl: string | number | null;
    conversion_rate: string | number | null;
    marketing_health_score: number | null;
    data_source: string;
  };
  ai_priorities: Array<{
    id: string;
    priority: string;
    title: string;
    recommendation: string;
    client_id: string | null;
    client_name: string | null;
  }>;
  client_performance: Array<{
    client_id: string;
    business_name: string;
    industry: string | null;
    spend: string | number;
    leads: number;
    cpl: string | number | null;
    health_score: number | null;
    data_source: string;
  }>;
  recent_recommendations: Array<{
    id: string;
    priority: string;
    title: string;
    recommendation: string;
    client_id: string | null;
    client_name: string | null;
  }>;
  pending_approvals: Array<{
    id: string;
    type: string;
    title: string;
    client_id: string;
    client_name: string;
    priority: string;
  }>;
  demo_mode: boolean;
};

export type Strategy = {
  id: string;
  client_id: string;
  title: string;
  current_situation: string;
  what_is_happening: string;
  key_problems: string[];
  opportunities: string[];
  strategy_summary: string;
  status: string;
  source: string;
  actions: StrategyAction[];
  created_at: string;
};

export type StrategyAction = {
  id: string;
  action: string;
  channel: string;
  objective: string;
  priority: string;
  estimated_effort: string;
  expected_outcome: string;
  required_assets: string[];
  deadline: string | null;
  status: "pending" | "approved" | "rejected" | "completed";
};

export type ContentGenerated = {
  hook: string;
  main_copy: string;
  cta: string;
  visual_concept: string;
  video_concept: string | null;
  hashtags: string[];
};

export type SocialPost = {
  id: string;
  client_id: string;
  platform: string;
  content_type: string;
  hook: string | null;
  main_copy: string | null;
  cta: string | null;
  visual_concept: string | null;
  video_concept: string | null;
  hashtags: string[];
  status: string;
  created_at: string;
};

export type CalendarItem = {
  id: string;
  client_id: string;
  title: string;
  platform: string;
  scheduled_for: string | null;
  social_post_id: string | null;
  notes: string | null;
  status: string;
  created_at: string;
};

export type Lead = {
  id: string;
  client_id: string;
  name: string;
  email: string | null;
  phone: string | null;
  source: string | null;
  campaign: string | null;
  ad: string | null;
  lead_score: number | null;
  score_explanation: {
    score?: number;
    reasons?: string[];
    based_on_available_data_only?: boolean;
    insufficient_data_note?: string | null;
  };
  status: string;
  notes: string | null;
  created_at: string;
  last_activity_at: string | null;
};

export type IntegrationStatus = {
  provider: string;
  status: "connected" | "not_connected" | "demo_data" | "sync_error";
  message: string;
  last_synced_at: string | null;
  account_label?: string | null;
  credentials_configured?: boolean;
  can_connect?: boolean;
};

export type Campaign = {
  id: string;
  client_id: string;
  ad_account_id: string | null;
  name: string;
  platform: string;
  status: string;
  objective: string | null;
  spend: string | number;
  metrics: {
    impressions?: number;
    clicks?: number;
    conversions?: number;
    leads?: number;
    ctr?: number;
    cpl?: number;
    external_campaign_id?: string;
    source?: string;
    note?: string;
    [key: string]: unknown;
  };
  data_source: string;
};

export const LEAD_STAGES = [
  "new",
  "contacted",
  "qualified",
  "interested",
  "meeting",
  "converted",
  "lost",
] as const;

export type Analytics = {
  client_id: string | null;
  period_days: number;
  comparison_period_days: number;
  data_source: string;
  demo_mode: boolean;
  current: {
    spend: string | number;
    leads: number;
    revenue: string | number;
    impressions: number;
    clicks: number;
    conversions: number;
    cpl: string | number | null;
    ctr: string | number | null;
    conversion_rate: string | number | null;
  };
  previous: {
    spend: string | number;
    leads: number;
    revenue: string | number;
    cpl: string | number | null;
    ctr: string | number | null;
    conversion_rate: string | number | null;
  };
  deltas: Record<string, number | null>;
  series: Record<string, Array<{ date: string; value: number }>>;
  content_performance: Array<{
    id: string;
    platform: string;
    content_type: string;
    hook: string | null;
    impressions: number | null;
    engagement: number | null;
    ctr: number | null;
    data_source: string;
    note: string | null;
  }>;
  campaign_performance: Array<{
    id: string;
    name: string;
    platform: string;
    spend: string | number;
    leads: number;
    cpl: string | number | null;
    ctr: number | null;
    status: string;
    data_source: string;
  }>;
  insufficient_data: string[];
  sections: {
    social?: Record<string, unknown>;
    campaigns?: Record<string, unknown>;
    leads?: { total?: number; funnel?: Record<string, number>; cpl?: number | null };
    conversions?: Record<string, unknown>;
  };
};

export type Recommendation = {
  id: string;
  organization_id: string;
  client_id: string | null;
  client_name: string | null;
  title: string;
  problem: string;
  evidence: string;
  recommendation: string;
  priority: string;
  expected_impact: string;
  status: "pending" | "approved" | "rejected" | "saved" | "completed";
  created_at: string;
  updated_at: string;
};

export type Report = {
  id: string;
  client_id: string;
  title: string;
  period_start: string;
  period_end: string;
  content: {
    executive_summary?: string;
    key_metrics?: Record<string, number | null | undefined>;
    growth?: string[];
    declines?: string[];
    deltas?: Record<string, number | null>;
    top_content?: Array<Record<string, unknown>>;
    worst_performing_content?: Array<Record<string, unknown>>;
    top_campaigns?: Array<Record<string, unknown>>;
    worst_campaigns?: Array<Record<string, unknown>>;
    lead_performance?: Record<string, unknown>;
    ai_insights?: string[];
    next_week_strategy?: string;
    insufficient_data?: string[];
    data_source?: string;
  };
  export_path: string | null;
  status: string;
  created_at: string;
  data_source?: string | null;
};

export type Competitor = {
  id: string;
  client_id: string;
  name: string;
  url: string | null;
  notes: string | null;
  observations: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type LeadScoreSummary = {
  total_leads: number;
  scored_leads: number;
  average_score: number | null;
  high_intent: number;
  medium_intent: number;
  low_intent: number;
  top_leads: Lead[];
  data_note: string;
};

export type AutonomySettings = {
  id: string;
  organization_id: string;
  client_id: string | null;
  autonomy_mode: "copilot" | "assisted" | "autonomous";
  maximum_daily_ad_spend: string | number;
  maximum_campaign_budget: string | number;
  maximum_budget_increase_percentage: string | number;
  maximum_budget_decrease_percentage: string | number;
  maximum_campaigns_per_day: number;
  maximum_creatives_per_day: number;
  maximum_posts_per_day: number;
  maximum_actions_per_day?: number;
  require_approval_for_financial_actions: boolean;
  require_approval_for_publishing: boolean;
  require_approval_for_campaign_creation: boolean;
  allowed_platforms: string[];
  allowed_actions: string[];
  automation_enabled: boolean;
  max_ai_iterations?: number;
  max_ai_actions_per_cycle?: number;
  max_execution_time?: number;
  max_failures_per_cycle?: number;
};

export type AutopilotRunStep = {
  key: string;
  label: string;
  status: string;
  detail?: string | null;
};

export type AutopilotRun = {
  id: string;
  organization_id: string;
  client_id: string;
  run_type: string;
  status: string;
  goal: string;
  budget: string | number | null;
  duration_days: number;
  platforms: string[];
  autonomy_mode: string | null;
  steps: AutopilotRunStep[];
  action_ids: string[];
  result: Record<string, unknown>;
  error: string | null;
  demo_mode: boolean;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type CreativeAsset = {
  id: string;
  client_id: string;
  campaign_id: string | null;
  name: string;
  asset_type: string;
  platform: string | null;
  prompt: string | null;
  provider: string | null;
  status: string;
  content: Record<string, unknown>;
  meta: Record<string, unknown>;
  data_source: string;
  created_at: string;
};

export type AIAction = {
  id: string;
  organization_id: string;
  client_id: string | null;
  action_type: string;
  agent: string;
  platform: string | null;
  target_id: string | null;
  description: string;
  reason: string;
  evidence: unknown[];
  expected_impact: string | null;
  estimated_cost: string | number | null;
  risk_level: string;
  priority: string;
  requires_approval: boolean;
  status: string;
  payload: Record<string, unknown>;
  previous_state: Record<string, unknown>;
  result: Record<string, unknown>;
  demo_mode: boolean;
  expires_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  executed_at: string | null;
  error: string | null;
  retry_count: number;
  created_at: string;
  updated_at: string;
};

export type AutopilotSummary = {
  autonomy_mode: "copilot" | "assisted" | "autonomous";
  automation_enabled: boolean;
  pending_approvals: number;
  executing: number;
  completed_today: number;
  failed_today: number;
  scheduled_posts: number;
  creatives_generated: number;
  optimizations_open: number;
  campaigns_monitored: number;
  demo_mode: boolean;
};
