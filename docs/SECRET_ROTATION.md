# Secret rotation

Production secrets must live in the deployment platform's secret store — never
in git, Docker layers, frontend source, or logs.

---

## Inventory

| Secret | Purpose | Notes |
|--------|---------|-------|
| `SECRET_KEY` | JWT signing | Invalidates all access tokens on rotate |
| `ENCRYPTION_KEY` | Fernet for OAuth tokens at rest | **Breaks stored provider tokens** — requires re-consent |
| `DATABASE_URL` / `DATABASE_URL_SYNC` | Postgres | Rotate DB user password + update URLs |
| `REDIS_URL` | Rate limits | Rotate Redis AUTH if used |
| `METRICS_TOKEN` | `/metrics` scrape auth | Update scrapers in lockstep |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | AI | Provider console rotate |
| `IMAGE_*` / `VIDEO_*` keys | Media | Provider console rotate |
| S3 / R2 access keys | Object storage | Prefer instance roles; rotate keys if used |
| `META_*` / `GOOGLE_*` OAuth client secrets | Ads OAuth | Update provider consoles + env |
| Billing provider secrets | Payments | **Not production-ready** until a real PSP is integrated |

Startup refuses weak/placeholder `SECRET_KEY` / `ENCRYPTION_KEY` in production.

---

## Rotate application secrets

```bash
# SECRET_KEY
openssl rand -hex 32

# ENCRYPTION_KEY (Fernet)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

1. Generate new value in secret manager.
2. Deploy API + worker with the new value (rolling if possible).
3. For `SECRET_KEY`: users re-login (refresh families may need logout-all).
4. For `ENCRYPTION_KEY`: stored Meta/Google tokens become unreadable — force
   disconnect/reconnect per org; do not invent tokens.

---

## Rotate database credentials

1. Create new DB role / password on the managed Postgres.
2. Update `DATABASE_URL` and `DATABASE_URL_SYNC`.
3. Restart migrate (no-op), API, worker.
4. Revoke old role after healthy `/health/ready`.

---

## Rotate OAuth client secrets

1. Create new secret in Meta / Google Cloud consoles.
2. Update env; keep old secret valid until deploy completes if the provider allows.
3. Confirm OAuth start + callback with production redirect URIs (no localhost).
4. Revoke old client secret.

---

## Rotate provider user tokens

Use product disconnect → reconnect (OAuth). Do not paste tokens into logs or tickets.

---

## Compromise response

```text
1. Activate AUTONOMOUS_KILL_SWITCH=true; set CANARY_ENABLED=false
2. Rotate compromised secret(s)
3. POST /auth/logout-all for affected users (or revoke refresh families)
4. Force provider reconnect if ENCRYPTION_KEY or OAuth client secret leaked
5. Review audit logs for the window of exposure
6. Confirm live ads still OFF unless a deliberate canary is re-authorized
```

Never log: `access_token`, `refresh_token`, `client_secret`, authorization codes,
developer tokens, or payment secrets.

---

## Related

- [`PRODUCTION_RUNBOOK.md`](PRODUCTION_RUNBOOK.md)
- [`.env.example`](../.env.example)
