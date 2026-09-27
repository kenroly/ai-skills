---
name: browser
description: Windows/macOS only. Do not use on Linux or Linux-based environments. Drive a real browser through GPMLogin antidetect profiles - open pages, read them, click, type, log in, scrape, screenshot. Use this whenever a Windows or macOS task needs a browser: "open X", "log into Y", "check this site", "fill this form", "scrape this page", "screenshot this URL", "vào web", "đăng nhập", or when a URL cannot be answered by a plain fetch (login walls, JS-rendered pages, captchas, dashboards). Also use for GPMLogin profile/group/proxy management.
argument-hint: "[profile name or url]"
---

# browser

Use this skill only on Windows or macOS hosts. Do not invoke it on Linux or
Linux-based environments; use the available browser/computer-use tools or
ordinary web fetch/search instead.

`gpm.py` wraps two things: the **GPMLogin Local API** (`http://127.0.0.1:9495/api/v1`)
for profile lifecycle, and **Playwright over CDP** for page interaction. Each command
reconnects to the already-running browser and exits, so you drive the page one step at a
time and read the output before deciding the next step.

Run it with the interpreter on PATH — no venv needed, `playwright` and `selenium` are
already installed system-wide:

```bash
python "$USERPROFILE/.claude/skills/browser/gpm.py" <command> [args]
```

Session state (active profile, CDP port, active tab) is remembered in
`%TEMP%\gpm-browser-skill\session.json`, so later commands need no ids.

## The loop

1. `profiles` — pick a profile. Profiles carry the proxy, fingerprint, and cookies, so
   pick one that matches the task (an account's own profile for that account's site).
2. `start <profile> --url <url>` — opens the browser, attaches, navigates.
3. `snapshot` — always read the page this way first. It prints the URL, title, and every
   visible interactive element with a `[eN]` ref, then the page text.
4. `click e12` / `fill e7 "text" --enter` — act on refs from the last snapshot.
5. `snapshot` again after anything that changes the page. **Refs go stale on navigation
   or re-render** — a stale ref errors and tells you to re-snapshot.
6. `stop` when finished, unless the user wants the browser left open.

```bash
python gpm.py start "ChatGPT - foo@gmail.com" --url chatgpt.com
python gpm.py snapshot
python gpm.py fill e14 "hello there" --enter
python gpm.py wait --text "Regenerate"
python gpm.py text
python gpm.py stop
```

## Targets

Anywhere a command takes a `target`, pass one of:

- `e12` — a ref from the last `snapshot` (preferred; unambiguous)
- CSS — `#email`, `button.primary`, `[data-testid=submit]`
- `text=Sign in` — Playwright text engine, matches visible text
- `//button[@id='x']` — xpath

## Commands

Profiles and groups (GPMLogin API, no browser needed):

| command | what it does |
|---|---|
| `profiles [--search X] [--page-size N]` | list profiles with id, group, browser, proxy |
| `profile <name\|id>` | full JSON including fingerprint |
| `groups`, `versions` | groups; installed browser versions |
| `create <name> [--group G] [--proxy P] [--os-type 1..5]` | new profile, fingerprint randomised |
| `delete <name\|id> [--hard]` | soft = trash, `--hard` wipes disk data |
| `start <name\|id>` | launch + attach. `--url`, `--window-size 1280,800`, `--window-pos 0,0`, `--scale 0.8`, `--port N`, `--skip-proxy-check`, `--args "--mute-audio"` |
| `stop [name\|id]` | close the browser (defaults to the active session) |
| `status` | active profile, tab count, current url |

Page interaction (needs a started profile):

| command | what it does |
|---|---|
| `snapshot [--load] [--json] [--max-text N]` | refs + page text. `--load` waits for network idle |
| `goto <url>` | navigate active tab (scheme optional) |
| `click <target> [--double] [--button right]` | scrolls into view, then clicks |
| `fill <target> <value> [--enter] [--slow]` | set an input. `--slow` types key-by-key |
| `press <key> [--target T]` | `Enter`, `Tab`, `Control+A`, `ArrowDown` |
| `select <target> <value>` / `check <target> [--off]` / `hover <target>` | form controls |
| `upload <target> <file...>` | set files on a file input |
| `text [target]` / `html [target]` | extract content |
| `eval "<js>" [--file f.js]` | run JS in the page, prints JSON result |
| `screenshot [path] [--full] [--target T]` | PNG; prints the path — then Read it |
| `wait [--text T] [--selector S] [--ms N] [--state attached]` | no flags = wait for network idle |
| `nav back\|forward\|reload`, `scroll [target] [--dy N]` | navigation |
| `tabs`, `tab <i>`, `newtab [url]`, `closetab` | tab management |
| `cookies [--values]` | names + domains; `--values` also prints secrets |

Every page command accepts `--tab N` (act on a specific tab), `--timeout S` (default 30),
and `--settle MS` (pause after the action so the page can react).

`wait --text` matches against the page's visible `innerText`, case-insensitively — it is
the reliable way to know an action landed. `wait --selector` waits for the element to be
visible; pass `--state attached` for elements that exist but stay hidden.

## Reading a page

`snapshot` is the cheap default and is usually enough. Reach for the others when it is not:

- `text` — long article or chat transcript; snapshot truncates at 4000 chars
- `eval` — structured extraction, e.g.
  `eval "[...document.querySelectorAll('.row')].map(r=>r.innerText)"`
- `screenshot` then Read the PNG — visual layout, canvas, captcha, or when the DOM lies
- `html <target>` — when you need attributes snapshot omits

## Gotchas

- **Chromium only.** Firefox profiles have no CDP endpoint; `start` works but page
  commands fail. Use a chrome/edge/brave profile for automation.
- **Tab indices are not the visual tab order** and shift when tabs open or close. Re-run
  `tabs` before using `--tab N`. With no `--tab`, commands act on the front tab, detected
  via `document.visibilityState`; if the window is minimised that detection is unreliable,
  so pass `--tab` explicitly for background work.
- **Refs live in the main frame.** Content inside an iframe never appears in `snapshot`.
  Find the frame with `eval "[...document.querySelectorAll('iframe')].map(f=>f.src)"`,
  then `goto` that URL directly, or drive the frame from `eval`.
- **`stop` closes a real browser window.** If the user was using that profile, ask first.
  It is idempotent — on an already-closed profile it reports `already closed`.
- **`start` on an already-open profile** returns the cached CDP info rather than reopening —
  safe to call to re-attach after the session file is lost.
- **Profiles with a proxy are slow to start** (proxy check first). `--skip-proxy-check`
  skips it when you already trust the proxy.
- **`delete --hard` is irreversible** and erases cookies/logins on disk. Confirm first.
  Right after `stop` the files can still be locked; the script retries for ~8s.
- If a command reports it cannot attach, the browser was closed outside the API — run
  `start` again.
- The API is localhost-only and unauthenticated by design; it needs GPMLogin running.
  If port 9495 is taken GPMLogin picks a random port and writes it to `http.port` in its
  AppData folder — the script finds it automatically.

## Human pacing

These are antidetect profiles, so the point is to look like a person. Prefer
`fill --slow` for credentials and search boxes, keep the default `--settle` pauses, and
use `wait --text` instead of hammering `snapshot` in a tight loop.
