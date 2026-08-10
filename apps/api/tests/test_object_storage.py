"""P1-2 — S3-compatible object storage."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings, get_settings
from app.storage import object_storage as store
from app.storage.object_storage import (
    LocalObjectStorage,
    S3ObjectStorage,
    StorageConfigurationError,
    StorageUnavailableError,
    build_key,
    get_object_storage,
    key_belongs_to_organization,
    set_object_storage,
)

BUCKET = "growthos-test-assets"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _prod_settings(**overrides) -> Settings:
    base = {
        "environment": "production",
        "secret_key": "9f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35c07bd18492af6c3e5",
        "encryption_key": "c07bd18492af6c3e59f2c41b7ae0d63528c1fb47e0a95d3ce7b6108fa24d9e35",
        "demo_mode": False,
        "ai_provider": "openai",
        "openai_api_key": "sk-test",
        "database_url": "postgresql+asyncpg://u:p@db:5432/growthos",
        "api_cors_origins": "https://app.example.com",
        "redis_url": "redis://cache:6379/0",
        "storage_backend": "s3",
        "s3_bucket": BUCKET,
        "s3_access_key_id": "AKIAEXAMPLE",
        "s3_secret_access_key": "secret",
        "metrics_token": "test-metrics-token-not-a-placeholder",
        "trusted_proxy_ips": "none",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _reset_storage_singleton():
    set_object_storage(None)
    yield
    set_object_storage(None)


@pytest.fixture
def s3(monkeypatch):
    """A real S3 protocol implementation (moto), so boto3 behaviour is exercised."""
    moto = pytest.importorskip("moto")
    import boto3

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        # Built through the real factory so the production client configuration
        # (SigV4, addressing style, retries) is what gets exercised.
        yield S3ObjectStorage(_prod_settings(s3_region="us-east-1"))


@pytest.fixture
def local(tmp_path):
    return LocalObjectStorage(root=tmp_path / "assets")


# --------------------------------------------------------------------------
# Backend selection: no silent fallback
# --------------------------------------------------------------------------


def test_production_refuses_local_storage(monkeypatch):
    """The audit finding: `get_object_storage()` returned local for every value."""
    monkeypatch.setattr(store, "get_settings", lambda: _prod_settings(storage_backend="local"))
    with pytest.raises(StorageConfigurationError) as exc:
        get_object_storage()
    assert "ephemeral" in str(exc.value)


def test_unknown_backend_raises_instead_of_degrading(monkeypatch):
    monkeypatch.setattr(
        store, "get_settings", lambda: _prod_settings(storage_backend="dropbox")
    )
    with pytest.raises(StorageConfigurationError) as exc:
        get_object_storage()
    assert "dropbox" in str(exc.value)


def test_s3_without_a_bucket_raises():
    with pytest.raises(StorageConfigurationError) as exc:
        S3ObjectStorage(_prod_settings(s3_bucket=""))
    assert "S3_BUCKET" in str(exc.value)


def test_half_configured_credentials_are_rejected():
    """A key without a secret usually means a typo, not an instance role."""
    with pytest.raises(StorageConfigurationError):
        S3ObjectStorage._build_client(_prod_settings(s3_secret_access_key=""))


def test_provider_is_not_hard_coded():
    """R2 / MinIO / Wasabi select the same adapter via STORAGE_BACKEND."""
    assert {"s3", "r2", "minio", "wasabi", "spaces"} <= store.S3_ALIASES


def test_custom_endpoint_is_honoured_for_non_aws_providers():
    client = S3ObjectStorage._build_client(
        _prod_settings(s3_endpoint_url="https://accountid.r2.cloudflarestorage.com")
    )
    assert "r2.cloudflarestorage.com" in client.meta.endpoint_url


def test_production_startup_fails_on_local_storage():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_prod_settings(storage_backend="local"))
    assert "STORAGE_BACKEND" in str(exc.value)

    validate_configuration(_prod_settings())  # s3 configured — must not raise


def test_production_startup_fails_without_a_bucket():
    from app.core.startup_checks import ConfigurationError, validate_configuration

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(_prod_settings(s3_bucket=""))
    assert "S3_BUCKET" in str(exc.value)


def test_development_may_use_local_storage(monkeypatch):
    monkeypatch.setattr(
        store,
        "get_settings",
        lambda: Settings(environment="development", storage_backend="local"),
    )
    assert get_object_storage().backend == "local"


def test_local_storage_is_marked_non_durable(local):
    assert local.durable is False
    assert S3ObjectStorage.durable is True


# --------------------------------------------------------------------------
# S3 round trip
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_then_download_returns_identical_bytes(s3):
    key = build_key(
        organization_id=uuid.uuid4(), client_id=uuid.uuid4(), kind="images", filename="a.png"
    )
    assert await s3.upload(PNG, key, "image/png") == key
    assert await s3.get_bytes(key) == PNG
    assert await s3.exists(key) is True
    assert await s3.content_type(key) == "image/png"


@pytest.mark.asyncio
async def test_delete_removes_the_object(s3):
    key = "organizations/o/clients/c/images/gone.png"
    await s3.upload(PNG, key, "image/png")
    await s3.delete(key)
    assert await s3.exists(key) is False
    assert await s3.get_bytes(key) is None


@pytest.mark.asyncio
async def test_missing_object_reads_as_none_not_an_error(s3):
    assert await s3.get_bytes("organizations/o/images/never-written.png") is None
    assert await s3.exists("organizations/o/images/never-written.png") is False


@pytest.mark.asyncio
async def test_deleting_a_missing_object_is_not_an_error(s3):
    await s3.delete("organizations/o/images/never-written.png")


@pytest.mark.asyncio
async def test_signed_url_is_time_limited_and_carries_no_static_secret(s3):
    key = "organizations/o/clients/c/videos/v.mp4"
    await s3.upload(b"\x00\x00\x00\x18ftypmp42" + b"0" * 32, key, "video/mp4")
    url = await s3.get_url(key)
    assert key in url
    assert "X-Amz-Expires" in url and "X-Amz-Signature" in url
    assert "testing" not in url, "the secret access key must never appear in a signed URL"


@pytest.mark.asyncio
async def test_health_check_fails_for_a_missing_bucket(s3):
    await s3.health_check()
    s3.bucket = "bucket-that-does-not-exist"
    with pytest.raises(StorageUnavailableError):
        await s3.health_check()


# --------------------------------------------------------------------------
# Failure surfacing — never claim a write that did not happen
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_failure_raises_rather_than_reporting_success():
    class BrokenClient:
        def put_object(self, **_):
            raise ConnectionError("network down")

    broken = S3ObjectStorage(_prod_settings(), client=BrokenClient())
    with pytest.raises(StorageUnavailableError):
        await broken.upload(PNG, "organizations/o/images/x.png", "image/png")


@pytest.mark.asyncio
async def test_transport_failure_is_distinguished_from_a_missing_object():
    class BrokenClient:
        def head_object(self, **_):
            raise ConnectionError("network down")

    broken = S3ObjectStorage(_prod_settings(), client=BrokenClient())
    with pytest.raises(StorageUnavailableError):
        # Returning False here would let a caller conclude the asset was lost.
        await broken.exists("organizations/o/images/x.png")


@pytest.mark.asyncio
async def test_media_job_fails_when_storage_rejects_the_upload(monkeypatch):
    """An unstorable image must not produce a COMPLETED job."""
    from app.models.enums import JobStatus
    from app.services.media_generation_service import MediaGenerationService

    class BrokenStorage(LocalObjectStorage):
        async def upload(self, data, key, content_type):
            raise StorageUnavailableError("bucket unreachable")

    class FakeJob:
        status = JobStatus.uploading
        error = None
        error_code = None
        retryable = False

    class FakeDB:
        async def flush(self):
            return None

    service = MediaGenerationService.__new__(MediaGenerationService)
    service.db = FakeDB()
    service._storage = BrokenStorage(root="/tmp/growthos-broken-storage-test")

    job = FakeJob()
    assert await service._persist(job, PNG, "organizations/o/images/x.png", "image/png") is False
    assert job.status == JobStatus.failed
    assert job.error_code == "STORAGE_UPLOAD_FAILED"
    assert "bucket unreachable" in job.error
    assert job.retryable is True


@pytest.mark.asyncio
async def test_job_fails_when_the_object_is_absent_after_a_successful_upload(tmp_path):
    """A backend that swallows writes must still not yield a COMPLETED job."""
    from app.models.enums import JobStatus
    from app.services.media_generation_service import MediaGenerationService

    class SilentlyDiscardingStorage(LocalObjectStorage):
        async def upload(self, data, key, content_type):
            return key  # reports success, writes nothing

    class FakeJob:
        status = JobStatus.uploading
        error = None
        error_code = None
        retryable = False

    class FakeDB:
        async def flush(self):
            return None

    service = MediaGenerationService.__new__(MediaGenerationService)
    service.db = FakeDB()
    service._storage = SilentlyDiscardingStorage(root=tmp_path)

    job = FakeJob()
    assert await service._persist(job, PNG, "organizations/o/images/x.png", "image/png") is False
    assert job.status == JobStatus.failed
    assert "could not be found" in job.error


# --------------------------------------------------------------------------
# Tenant isolation
# --------------------------------------------------------------------------


def test_keys_encode_organization_ownership():
    org, client = uuid.uuid4(), uuid.uuid4()
    key = build_key(organization_id=org, client_id=client, kind="images", filename="a.png")
    assert key.startswith(f"organizations/{org}/clients/{client}/images/")
    assert key_belongs_to_organization(key, org) is True
    assert key_belongs_to_organization(key, uuid.uuid4()) is False


def test_prefix_confusion_does_not_grant_access():
    """`organizations/abc-evil/...` must not match organization `abc`."""
    assert key_belongs_to_organization("organizations/abc-evil/images/a.png", "abc") is False
    assert key_belongs_to_organization("organizations/abc/images/a.png", "abc") is True


def test_missing_key_is_not_owned_by_anyone():
    assert key_belongs_to_organization(None, uuid.uuid4()) is False
    assert key_belongs_to_organization("", uuid.uuid4()) is False


def test_key_builder_strips_path_traversal():
    """Attacker-controlled segments must not add path levels or climb out."""
    key = build_key(
        organization_id="org", client_id="../../etc", kind="images", filename="../passwd"
    )
    assert ".." not in key
    assert key.count("/") == 5, f"unexpected extra path levels: {key}"
    assert key_belongs_to_organization(key, "org")


@pytest.mark.asyncio
async def test_local_storage_rejects_traversal_keys(local):
    """A key that resolves outside the storage root is refused outright."""
    with pytest.raises(ValueError):
        await local.upload(PNG, "../../../etc/passwd", "image/png")
    assert not (local.root.parent / "etc" / "passwd").exists()


@pytest.mark.asyncio
async def test_media_endpoint_rejects_a_key_outside_the_tenant_prefix():
    """A tampered storage_key must not read another tenant's object."""
    import uuid as _uuid

    from httpx import ASGITransport, AsyncClient

    from app.core.security import hash_password
    from app.db.session import AsyncSessionLocal
    from app.main import app
    from app.models.automation import CreativeAsset
    from app.models.client import Client
    from app.models.enums import MemberRole
    from app.models.organization import Organization, OrganizationMember
    from app.models.user import User

    password = "Str0ng-Test-Passw0rd!"
    suffix = _uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        victim_org = Organization(name=f"Victim {suffix}", slug=f"victim-{suffix}", demo_mode=False)
        attacker_org = Organization(name=f"Attacker {suffix}", slug=f"attacker-{suffix}", demo_mode=False)
        db.add_all([victim_org, attacker_org])
        await db.flush()

        email = f"attacker-{suffix}@evil.test.com"
        user = User(email=email, hashed_password=hash_password(password), full_name="Attacker")
        db.add(user)
        await db.flush()
        db.add(OrganizationMember(organization_id=attacker_org.id, user_id=user.id, role=MemberRole.owner))

        client_row = Client(organization_id=attacker_org.id, business_name="A", industry="saas")
        db.add(client_row)
        await db.flush()

        # An attacker-owned row pointing at the victim's storage prefix.
        asset = CreativeAsset(
            organization_id=attacker_org.id,
            client_id=client_row.id,
            name="stolen",
            asset_type="image",
            storage_key=f"organizations/{victim_org.id}/clients/x/images/secret.png",
            mime_type="image/png",
            status="completed",
        )
        db.add(asset)
        await db.commit()
        asset_id = asset.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        token = (await http.post("/api/v1/auth/login", json={"email": email, "password": password})).json()
        response = await http.get(
            f"/api/v1/creative/media/{asset_id}",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )

    assert response.status_code == 404, "a cross-tenant key must never be served"


# --------------------------------------------------------------------------
# Reports go through storage, not the local disk
# --------------------------------------------------------------------------


def test_report_service_no_longer_writes_to_the_local_disk():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/services/report_service.py").read_text()
    assert 'Path(settings.storage_local_path) / "reports"' not in source
    assert "build_key(" in source and "get_object_storage()" in source


@pytest.mark.asyncio
async def test_report_export_round_trips_through_object_storage(s3, monkeypatch):
    import uuid as _uuid
    from datetime import date

    from app.models.ai_ops import Report
    from app.services.report_service import ReportService

    monkeypatch.setattr(store, "get_settings", lambda: _prod_settings())
    set_object_storage(s3)

    org_id, client_id = _uuid.uuid4(), _uuid.uuid4()
    report = Report(
        id=_uuid.uuid4(),
        organization_id=org_id,
        client_id=client_id,
        title="Weekly performance",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 7),
        content={"executive_summary": "Spend held flat.", "next_week_strategy": "Hold."},
    )

    service = ReportService.__new__(ReportService)
    key = await service._store_pdf(org_id, client_id, report)

    assert key is not None
    assert key_belongs_to_organization(key, org_id)
    assert await s3.exists(key)

    report.export_path = key
    data, media_type, extension = await service.load_export(org_id, client_id, report)
    assert data.startswith(b"%PDF") or extension == "txt"
    assert media_type in {"application/pdf", "text/plain"}


@pytest.mark.asyncio
async def test_report_export_is_none_when_storage_fails(monkeypatch):
    """A failed upload must not leave an export_path that will 404 later."""
    import uuid as _uuid
    from datetime import date

    from app.models.ai_ops import Report
    from app.services.report_service import ReportService

    class BrokenStorage(LocalObjectStorage):
        async def upload(self, data, key, content_type):
            raise StorageUnavailableError("bucket unreachable")

    set_object_storage(BrokenStorage(root="/tmp/growthos-broken-reports"))

    report = Report(
        id=_uuid.uuid4(),
        organization_id=_uuid.uuid4(),
        client_id=_uuid.uuid4(),
        title="Weekly",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 7),
        content={},
    )
    service = ReportService.__new__(ReportService)
    assert await service._store_pdf(report.organization_id, report.client_id, report) is None


@pytest.mark.asyncio
async def test_report_export_from_another_tenant_is_refused(s3, monkeypatch):
    import uuid as _uuid

    from app.models.ai_ops import Report
    from app.services.report_service import ReportService

    monkeypatch.setattr(store, "get_settings", lambda: _prod_settings())
    set_object_storage(s3)

    victim_org = _uuid.uuid4()
    key = build_key(organization_id=victim_org, client_id=None, kind="reports", filename="r.pdf")
    await s3.upload(b"%PDF-1.4 secret", key, "application/pdf")

    report = Report(
        id=_uuid.uuid4(),
        organization_id=_uuid.uuid4(),
        client_id=_uuid.uuid4(),
        title="x",
        content={},
        export_path=key,
    )
    service = ReportService.__new__(ReportService)
    with pytest.raises(FileNotFoundError):
        await service.load_export(report.organization_id, report.client_id, report)
