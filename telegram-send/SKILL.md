---
name: telegram-send
description: Send messages and files to the user's saved Telegram chat through Vaultwarden credentials. For Orimise POS APKs, publish a direct download link and send the release details. Use when the user asks for Telegram delivery.
---

# Telegram Send

Use `scripts/send-telegram.ps1` for Telegram delivery. The helper reads the item
`Telegram delivery (Orimise)` from the Vaultwarden `Agent Drop` collection:

- login password: bot token
- login username: default chat ID

Never print, echo, persist, or include the bot token in commands, responses, or
diagnostic output. Do not copy credentials into the skill directory.

## Orimise POS APK: direct download link

The user's preferred delivery is a Telegram message containing a hosted APK
link and release metadata, not a document attachment or numbered file parts.
Apply this to Orimise POS APK requests such as "gửi tele cái APK". Use an
attachment or split files only if the user explicitly asks for that format.
This preference does not change delivery of ordinary documents or other files.

Use the APK selected in the current task; do not reuse an older release URL,
date, hash, or size from a sample message. If it already has a working public
download URL, verify and reuse that URL. Otherwise publish the requested POS
APK through the existing downloads service as part of the requested delivery.

Existing hosting setup (verify against the current configuration before use):

- URL prefix: `https://pos.orimise.com/downloads/`.
- Host: `thedat@160.191.175.143`, SSH port `61789`, key
  `$env:USERPROFILE/.ssh/orimise_vps`.
- Canonical config: `E:/Code/orimise/devops/services/edge/` locally and
  `/home/thedat/docker/devops/services/edge/` on the host.
- Nginx serves `/srv/downloads/` from the persistent Docker volume
  `edge_downloads`, mounted read-only in `edge-nginx-1`.

Compute the local file's SHA-256 and size, then name it
`orimise-pos-YYYYMMDD-HASH8.apk`, using the release date and first eight lowercase
hash characters. Upload to a temporary file on the host. Copy it into
`edge_downloads` through a temporary container with that volume mounted writable;
use an image already present on the host, no network, and a read-only mount of
the uploaded source. Verify the copied hash before renaming it to the final
filename and set readable permissions (`0644`). Preserve existing releases.
No application rebuild, nginx restart, or infrastructure reconfiguration is
needed. Remove only the temporary upload after successful publication.

Before sending, GET the public URL and verify HTTP 200, byte count, and SHA-256
of the downloaded bytes against the local APK. Do not send a broken or unverified
link. If publishing fails, diagnose the actual failure; do not silently switch
to split files or send a success message.

Send this Vietnamese message with `send-telegram.ps1 -Message`, retaining its
line structure and filling every value from the actual release:

```text
Orimise POS — APK release YYYY-MM-DD
Tải: https://pos.orimise.com/downloads/orimise-pos-YYYYMMDD-HASH8.apk
Dung lượng: SIZE MB (universal, chạy mọi máy Android)
SHA-256: FULL_SHA256
API: https://pos-api.orimise.com
Lưu ý: bản này ký bằng debug key (chưa có key phát hành), cài trực tiếp vẫn chạy bình thường.
```

Use one decimal for size (bytes / 1024 / 1024, matching the user's template).
Verify the package/version, supported Android architectures and signing details;
adapt the universal/compatibility wording for restricted builds. Include the
debug-key note only when the APK actually uses debug signing. Preserve Vietnamese
diacritics and real newlines when passing the message to PowerShell. Report the
confirmed Telegram message ID and download link after delivery.

## Other files and text

Send a local file:

```powershell
powershell -File "$env:USERPROFILE\.codex\skills\telegram-send\scripts\send-telegram.ps1" -FilePath "C:\absolute\artifact.apk" -Caption "Orimise POS release"
```

Send text:

```powershell
powershell -File "$env:USERPROFILE\.codex\skills\telegram-send\scripts\send-telegram.ps1" -Message "Deployment complete"
```

Only override `-ChatId` when the user explicitly names another destination.
Sending is an external side effect: do it only when the user's current request
authorizes delivery. Report the Telegram message ID after success. If the vault
is unavailable, start the user's local Vaultwarden Docker stack and retry once.
For other files rejected because they exceed the Bot API limit, use
`scripts/split-and-send-telegram.ps1` when split delivery fits the user's request.
It sends a manifest plus numbered parts and removes temporary parts afterward.
The Orimise POS APK workflow above takes precedence over this fallback.
