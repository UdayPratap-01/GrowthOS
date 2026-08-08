from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class KPIBlock(BaseModel):
    total_clients: int
    total_leads: int
    total_ad_spend: Decimal
    estimated_revenue: Decimal
    average_cpl: Decimal | None
    conversion_rate: Decimal | None
    marketing_health_score: int | None
    data_source: str


class AIPriorityItem(BaseModel):
    id: UUID
    priority: str
    title: str
    recommendation: str
    client_id: UUID | None = None
    client_name: str | None = None


class ClientPerformanceCard(BaseModel):
    client_id: UUID
    business_name: str
    industry: str | None
    spend: Decimal
    leads: int
    cpl: Decimal | None
    health_score: int | None
    data_source: str


class PendingApproval(BaseModel):
    id: UUID
    type: str
    title: str
    client_id: UUID
    client_name: str
    priority: str


class DashboardOut(BaseModel):
    kpis: KPIBlock
    ai_priorities: list[AIPriorityItem]
    client_performance: list[ClientPerformanceCard]
    recent_recommendations: list[AIPriorityItem]
    pending_approvals: list[PendingApproval]
    demo_mode: bool
