import enum


class MemberRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


class ClientStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class ConnectionStatus(str, enum.Enum):
    connected = "connected"
    not_connected = "not_connected"
    demo_data = "demo_data"
    sync_error = "sync_error"


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    interested = "interested"
    meeting = "meeting"
    converted = "converted"
    lost = "lost"


class ActionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    completed = "completed"


class Priority(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class RecommendationStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    saved = "saved"
    completed = "completed"


class PerformanceRecommendationStatus(str, enum.Enum):
    """Lifecycle for analysis-only performance recommendations (Milestone 2)."""

    new = "NEW"
    reviewed = "REVIEWED"
    approved = "APPROVED"
    rejected = "REJECTED"
    expired = "EXPIRED"


class DataSource(str, enum.Enum):
    demo = "demo"
    live = "live"


class AutonomyMode(str, enum.Enum):
    copilot = "copilot"
    assisted = "assisted"
    autonomous = "autonomous"


class AIActionType(str, enum.Enum):
    create_campaign = "CREATE_CAMPAIGN"
    create_ad_set = "CREATE_AD_SET"
    create_ad = "CREATE_AD"
    update_campaign = "UPDATE_CAMPAIGN"
    update_budget = "UPDATE_BUDGET"
    pause_campaign = "PAUSE_CAMPAIGN"
    resume_campaign = "RESUME_CAMPAIGN"
    create_creative = "CREATE_CREATIVE"
    generate_image = "GENERATE_IMAGE"
    generate_video = "GENERATE_VIDEO"
    create_content = "CREATE_CONTENT"
    schedule_content = "SCHEDULE_CONTENT"
    publish_content = "PUBLISH_CONTENT"
    update_content = "UPDATE_CONTENT"
    generate_report = "GENERATE_REPORT"
    generate_recommendation = "GENERATE_RECOMMENDATION"
    create_lead_action = "CREATE_LEAD_ACTION"
    send_notification = "SEND_NOTIFICATION"
    optimize_campaign = "OPTIMIZE_CAMPAIGN"
    generate_creative_variations = "GENERATE_CREATIVE_VARIATIONS"


class AIActionStatus(str, enum.Enum):
    pending = "PENDING"
    approved = "APPROVED"
    rejected = "REJECTED"
    expired = "EXPIRED"
    executing = "EXECUTING"
    completed = "COMPLETED"
    failed = "FAILED"
    cancelled = "CANCELLED"
    scheduled = "SCHEDULED"


class RiskLevel(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class JobStatus(str, enum.Enum):
    queued = "queued"
    submitted = "submitted"
    generating = "generating"
    processing = "processing"
    downloading = "downloading"
    uploading = "uploading"
    running = "running"
    waiting_approval = "waiting_approval"
    scheduled = "scheduled"
    completed = "completed"
    failed = "failed"
    # Transient failure awaiting its next attempt (run_after gates the retry).
    retrying = "retrying"
    cancelled = "cancelled"


class HealthCategory(str, enum.Enum):
    excellent = "excellent"
    good = "good"
    needs_attention = "needs_attention"
    poor = "poor"
    critical = "critical"
