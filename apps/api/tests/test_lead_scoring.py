from app.ai.agents.lead_agent import LeadAgent, LeadScoreRequest
from app.ai.providers.mock import MockAIProvider


def test_deterministic_score_does_not_fabricate_behavior():
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(
        LeadScoreRequest(name="Test Lead", email="a@b.com", source="Meta Ads", campaign="Prospecting")
    )
    assert 0 <= result.score <= 100
    assert result.based_on_available_data_only is True
    assert any("email" in r.lower() or "Email" in r for r in result.reasons)
    assert result.insufficient_data_note is not None
    assert "fabricat" not in " ".join(result.reasons).lower()


def test_empty_lead_states_insufficient_data():
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(LeadScoreRequest(name="Sparse"))
    assert result.score >= 0
    assert any("Insufficient data" in r for r in result.reasons)
