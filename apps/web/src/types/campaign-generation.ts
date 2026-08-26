/**
 * P2-A campaign engine contracts.
 *
 * Mirrors `apps/api/app/schemas/campaign_generation.py`. Field names stay in
 * snake_case so a response can be used without a translation layer.
 *
 * Two absences are deliberate and must survive future edits: there is no
 * `published` review status, and there are no performance fields (CTR, CPL,
 * ROAS, revenue). Neither exists on the server, so neither can be rendered —
 * which is the point.
 */

export type VariationAxis =
  | "hook"
  | "visual"
  | "offer"
  | "cta"
  | "tone"
  | "composition"
  | "format"
  | "audience_angle";

export type CreativeType = "image" | "video" | "copy";

/** The only review states the UI may display. */
export const REVIEW_STATUSES = [
  "DRAFT",
  "GENERATING",
  "READY_FOR_REVIEW",
  "APPROVED",
  "REJECTED",
  "READY_TO_PUBLISH",
] as const;

export type ReviewStatus = (typeof REVIEW_STATUSES)[number];

export type Evidence = {
  claim: string;
  source: string;
  value: string | null;
};

export type CampaignStrategy = {
  current_situation: string;
  problem: string;
  opportunity: string;
  target_audience: string;
  positioning: string;
  core_message: string;
  offer_strategy: string;
  creative_strategy: string;
  channel_strategy: string;
  campaign_objective: string;
  success_metrics: string[];
  risks: string[];
  data_limitations: string[];
  evidence: Evidence[];
};

export type MediaProviderStatus = {
  image_provider: string;
  image_configured: boolean;
  video_provider: string;
  video_configured: boolean;
  storage_backend: string;
  demo_mode: boolean;
  message: string;
};

export type PlatformAvailability = {
  key: string;
  label: string;
  aspect_ratios: string[];
  default_image_ratio: string;
  default_video_ratio: string;
  placements: string[];
  supports_video: boolean;
  headline_max_chars: number;
  primary_text_max_chars: number;
  connected: boolean;
  connection_status: string;
  publishing_supported: boolean;
  notes: string;
};

export type ObjectiveOption = {
  key: string;
  label: string;
  description: string;
  optimization: string;
  success_metrics: string[];
};

export type AspectRatioOption = {
  key: string;
  label: string;
  width: number;
  height: number;
  usage: string;
  orientation: string;
};

export type GenerationLimits = {
  max_concepts: number;
  max_images: number;
  max_videos: number;
  max_variations: number;
};

export type CampaignGeneratorOptions = {
  platforms: PlatformAvailability[];
  objectives: ObjectiveOption[];
  aspect_ratios: AspectRatioOption[];
  limits: GenerationLimits;
  media: MediaProviderStatus;
};

/** One row of the "Strategy ✓ / Images 2/3" checklist, as recorded by the worker. */
export type GenerationStage = {
  key: string;
  label: string;
  status: string;
  detail: string | null;
  completed: number;
  total: number;
};

export type CampaignGenerationRun = {
  id: string;
  organization_id: string;
  client_id: string;
  brief_id: string | null;
  campaign_id: string | null;
  status: string;
  platform: string;
  objective: string;
  stages: GenerationStage[];
  result: Record<string, unknown>;
  data_limitations: string[];
  error: string | null;
  error_code: string | null;
  background_job_id: string | null;
  concept_quantity: number;
  image_quantity: number;
  video_quantity: number;
  variation_quantity: number;
  demo_mode: boolean;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  terminal: boolean;
  poll_url: string | null;
};

export type CampaignBrief = {
  id: string;
  client_id: string;
  campaign_name: string;
  platform: string;
  objective: string;
  offer: string | null;
  audience: string | null;
  pain_points: string[];
  value_proposition: string | null;
  messaging_angle: string | null;
  tone: string | null;
  brand_constraints: string[];
  total_budget: string | null;
  daily_budget: string | null;
  monthly_budget: string | null;
  currency: string;
  success_metrics: string[];
  creative_direction: string | null;
  cta: string | null;
  data_limitations: string[];
  strategy: Record<string, unknown>;
  data_source: string;
  created_at: string;
};

/**
 * A generated file, or the reason there is not one.
 *
 * `url` is absent unless the bytes are actually stored, so a missing url must
 * render as its `status` and never as a broken image.
 */
export type ConceptAsset = {
  id: string | null;
  job_id: string | null;
  kind: "image" | "video";
  status: string;
  url: string | null;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  aspect_ratio: string | null;
  provider: string | null;
  error: string | null;
  error_code: string | null;
  retryable: boolean;
  demo: boolean;
};

export type VisualDirection = {
  composition?: string;
  subject?: string;
  environment?: string;
  lighting?: string;
  style?: string;
  brand_elements?: string[];
  text_overlay?: string | null;
};

export type CreativeVariation = {
  id: string;
  parent_concept_id: string;
  reference: string;
  axis: string;
  hypothesis: string;
  creative_type: string;
  hook: string | null;
  primary_text: string | null;
  headline: string | null;
  description: string | null;
  cta: string | null;
  tone: string | null;
  audience: string | null;
  aspect_ratio: string | null;
  visual_direction: VisualDirection;
  image_prompt: string | null;
  video_prompt: string | null;
  negative_constraints: string[];
  status: string;
  archived_at: string | null;
  data_source: string;
  created_at: string;
  assets: ConceptAsset[];
};

export type CreativeConcept = {
  id: string;
  client_id: string;
  campaign_id: string | null;
  brief_id: string | null;
  reference: string;
  angle: string;
  hook: string | null;
  primary_text: string | null;
  headline: string | null;
  description: string | null;
  cta: string | null;
  tone: string | null;
  audience: string | null;
  objective: string | null;
  platform: string | null;
  visual_direction: VisualDirection;
  image_prompt: string | null;
  video_prompt: string | null;
  negative_constraints: string[];
  aspect_ratios: string[];
  status: string;
  archived_at: string | null;
  data_limitations: string[];
  data_source: string;
  created_at: string;
  assets: ConceptAsset[];
  variations: CreativeVariation[];
};

export type GeneratedAdSet = {
  id: string;
  name: string;
  audience: string | null;
  daily_budget: string | null;
  optimization: string | null;
  placements: string[];
  status: string;
};

export type GeneratedAd = {
  id: string;
  ad_set_id: string;
  name: string;
  concept_id: string | null;
  variation_id: string | null;
  creative_asset_id: string | null;
  headline: string | null;
  primary_text: string | null;
  cta: string | null;
  destination: string | null;
  status: string;
};

export type CampaignApproval = {
  review_status: string;
  approved_by: string | null;
  approved_at: string | null;
  approval_comment: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  /** Only a real integration sets this. Null means nothing was published. */
  external_id: string | null;
  can_approve: boolean;
};

export type GeneratedCampaign = {
  id: string;
  client_id: string;
  name: string;
  platform: string;
  objective: string | null;
  review_status: string;
  status: string;
  audience: string | null;
  total_budget: string | null;
  daily_budget: string | null;
  monthly_budget: string | null;
  currency: string;
  generated_by_ai: boolean;
  data_source: string;
  created_at: string;
};

export type CampaignPackage = {
  campaign: GeneratedCampaign | null;
  brief: CampaignBrief | null;
  strategy: CampaignStrategy | null;
  concepts: CreativeConcept[];
  ad_sets: GeneratedAdSet[];
  ads: GeneratedAd[];
  approval: CampaignApproval | null;
  run: CampaignGenerationRun | null;
  data_limitations: string[];
  media: MediaProviderStatus | null;
  publishing_note: string;
};

export type CampaignGenerateRequest = {
  client_id: string;
  platform: string;
  objective: string;
  campaign_name?: string | null;
  total_budget?: string | null;
  daily_budget?: string | null;
  monthly_budget?: string | null;
  currency?: string;
  duration_days?: number;
  offer?: string | null;
  audience?: string | null;
  tone?: string | null;
  cta?: string | null;
  concept_quantity: number;
  image_quantity: number;
  video_quantity: number;
  variation_quantity: number;
  aspect_ratios: string[];
  idempotency_key?: string;
};
