---
name: vault-save
description: Save a credential this machine just created into the user's self-hosted Vaultwarden. Use whenever you generate or set a password, DB user, API key, service account, or token that the user will need again — new Postgres/MySQL/Redis instance, seeded admin account, generated API key, created service principal. Also use when the user says "save this password", "lưu credential này", "cất vào vault", or asks where a credential you made went.
argument-hint: "[name] [--username u] [--uri host:port] [--notes text]"
---

# vault-save

Writes a login item into the **Agent Drop** collection of the user's self-hosted
Vaultwarden at `https://localhost:8443`.

The user set this up because they cannot remember scattered credentials. A password you
generate and mention only in chat is lost the moment the session ends — put it in the vault.

## When to use

Use it right after you create a credential, in the same turn, before reporting success:

- Standing up a database and setting its user/password (Postgres, MySQL, Mongo, Redis)
- Seeding an admin or service account in an app you just deployed
- Generating an API key, webhook secret, or signing token
- Any `docker compose` stack whose `.env` you just filled with fresh secrets

Do **not** use it for values that are already in version control, public config, or that
the user explicitly called throwaway.

## How to call it

Let the vault generate the password — it produces a strong one and prints it back for you
to paste into the config you are writing:

```powershell
powershell -File "$env:USERPROFILE\vaultwarden\vault-save.ps1" -Name "orimise-db (local)" -Username postgres -GeneratePassword -Uri "localhost:55432" -Notes "docker compose orimise, tao 2026-08-10"
```

If the password already exists (the user chose it, or a tool printed it), pass it through:

```powershell
powershell -File "$env:USERPROFILE\vaultwarden\vault-save.ps1" -Name "Redis dev" -Password "<value>" -Uri "localhost:6379"
```

Parameters: `-Name` (required), `-Username`, `-Password` or `-GeneratePassword`, `-Uri`,
`-Notes`, `-CollectionName` (defaults to `Agent Drop`).

## Naming

The name is what the user scans months later. Include what it is and where it lives:

- Good: `pos-postgres (local docker)`, `orimise VPS root`, `Stripe test key (orimise)`
- Bad: `password`, `db`, `new credential`

Put the reconstruction details in `-Notes`: which compose project, which port, when, why.

## Failure modes

**`Khong thay collection 'Agent Drop'`** — the agent account is not confirmed in the
organization. The user must open `https://localhost:8443` → org → Members → the pending
member → **Confirm**. You cannot do this for them; it needs their vault key.

**`Chua nap credential agent`** — the DPAPI credential file is missing. The user runs
`vault-save.ps1 -SetupCredential` themselves and types the agent account password. Never
ask them to paste that password into chat, and never write it into a script.

**`Login that bai`** — the stored agent password no longer matches. Same fix: they re-run
`-SetupCredential`.

**Vault unreachable** — the stack runs on Docker Desktop. Check with
`docker ps --filter name=vaultwarden`. If it is down, `docker compose -f "$env:USERPROFILE\vaultwarden\docker-compose.yml" up -d`.

## Boundaries

The agent account can read and write **only** the `Agent Drop` collection. It cannot see the
user's personal vault items, and it must stay that way — do not log in as, or ask for, the
user's own account.

There is no read helper yet. To retrieve a stored credential, the user opens the vault. If a
task needs a value back, ask them for it rather than reaching for their master password.

After saving, tell the user the item name you used, so they can find it. Report the generated
password in your reply too — they may need it before they next open the vault.
