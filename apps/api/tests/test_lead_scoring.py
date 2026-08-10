from app.ai.agents.lead_agent import LeadAgent, LeadScoreRequest
from app.ai.providers.mock import MockAIProvider

# Behavioural signals the product does not collect. The scorer must never claim any of them.
FABRICATION_MARKERS = (
    "page visit",
    "pricing page",
    "website activity",
    "email open",
    "form behavior",
    "form behaviour",
    "clicked",
    "browsed",
    "viewed our",
)


def test_deterministic_score_does_not_fabricate_behavior():
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(
        LeadScoreRequest(name="Test Lead", email="a@b.com", source="Meta Ads", campaign="Prospecting")
    )
    assert 0 <= result.score <= 100
    assert result.based_on_available_data_only is True
    assert any("email" in r.lower() for r in result.reasons)
    assert "fabricat" not in " ".join(result.reasons).lower()


def test_empty_lead_states_insufficient_data():
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(LeadScoreRequest(name="Sparse"))
    assert result.score >= 0
    assert result.data_limitations, "a lead with no data must declare its limitations"
    assert any("Insufficient data" in d for d in result.data_limitations)
    assert result.evidence == ["Insufficient data."]


def test_score_declares_deterministic_method():
    """P0-7: the output must state that no AI model produced the score."""
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(LeadScoreRequest(name="Lead", email="a@b.com"))
    assert result.method == "deterministic_rules"
    assert "deterministic" in result.method_label.lower()


def test_structured_output_contains_required_fields():
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(
        LeadScoreRequest(name="Lead", email="a@b.com", phone="+15550001111", source="meta", campaign="c1", ad="a1")
    )
    payload = result.model_dump()
    for field in ("score", "reasons", "evidence", "data_limitations"):
        assert field in payload, f"missing required field {field}"
    assert 0 <= payload["score"] <= 100
    assert payload["evidence"], "evidence must cite the fields actually used"


def test_evidence_only_cites_supplied_fields():
    """Evidence must be traceable to real input, never invented."""
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(LeadScoreRequest(name="Lead", email="a@b.com"))
    assert any("email=a@b.com" in e for e in result.evidence)
    assert not any("phone=" in e for e in result.evidence)
    assert not any("campaign=" in e for e in result.evidence)
    blob = " ".join(result.reasons + result.evidence).lower()
    for marker in FABRICATION_MARKERS:
        assert marker not in blob, f"scorer must not claim {marker!r}"


def test_untracked_behavior_is_reported_as_a_limitation_not_a_signal():
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(LeadScoreRequest(name="Lead", email="a@b.com"))
    limitations = " ".join(result.data_limitations).lower()
    assert "not tracked" in limitations
    assert "insufficient data" in limitations


def test_recorded_activities_are_credited_as_evidence():
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(
        LeadScoreRequest(name="Lead", email="a@b.com", known_activities=["Replied to outreach email"])
    )
    assert any("Replied to outreach email" in e for e in result.evidence)
    assert not any("No recorded lead activities" in d for d in result.data_limitations)


def test_score_is_capped_at_100():
    agent = LeadAgent(MockAIProvider())
    result = agent.deterministic_score(
        LeadScoreRequest(
            name="Maximal",
            email="a@b.com",
            phone="+15550001111",
            source="meta",
            campaign="c1",
            ad="a1",
            notes="lots of context",
            known_activities=[f"activity {i}" for i in range(20)],
        )
    )
    assert result.score == 100


def test_orchestrator_exposes_deterministic_name_only():
    """The misleading `score_lead` alias must be gone so callers cannot imply AI scoring."""
    from app.ai.orchestrator import AIOrchestrator

    assert hasattr(AIOrchestrator, "score_lead_deterministic")
    assert not hasattr(AIOrchestrator, "score_lead")


def test_no_ai_lead_scoring_label_in_frontend():
    from pathlib import Path

    web = Path(__file__).resolve().parents[3] / "apps" / "web" / "src"
    offenders = []
    for path in web.rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        if "AI Lead Scoring" in text or "AI scoring" in text:
            offenders.append(str(path))
    assert not offenders, f"Deterministic scoring must not be labelled AI: {offenders}"
