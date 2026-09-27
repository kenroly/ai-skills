#!/usr/bin/env python3
"""Drive GPMLogin antidetect profiles from the command line.

Two layers:
  * GPMLogin Local API (http://127.0.0.1:9495/api/v1) - profile/group/proxy CRUD,
    start/stop a browser.
  * Playwright over CDP - page interaction against the already-running browser.

Every browser command reconnects to the live browser and exits, so commands are
independent: run them one at a time and inspect the output between steps.
Session state (which profile, which CDP port, which tab) lives in a temp file.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

DEFAULT_PORT = 9495
PORT_FILES = [
    Path(os.environ.get("APPDATA", "")) / "GPMLoginGlobal" / "http.port",
    Path(os.environ.get("LOCALAPPDATA", "")) / "GPMLoginGlobal" / "http.port",
    Path(os.environ.get("APPDATA", "")) / "GPMLogin" / "http.port",
    Path(os.environ.get("LOCALAPPDATA", "")) / "GPMLogin" / "http.port",
]
STATE_FILE = Path(tempfile.gettempdir()) / "gpm-browser-skill" / "session.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def out(msg=""):
    print(msg, flush=True)


def die(msg, code=1):
    print("ERROR: " + str(msg), file=sys.stderr, flush=True)
    sys.exit(code)

def load_state():
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), "utf-8")


def _api_alive(port):
    try:
        url = "http://127.0.0.1:%d/api/v1/groups?page_size=1" % port
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def api_port():
    """Default port, else the port GPMLogin wrote to http.port."""
    state = load_state()
    cached = state.get("api_port")
    if cached and _api_alive(cached):
        return cached
    candidates = [DEFAULT_PORT]
    for f in PORT_FILES:
        try:
            candidates.append(int(f.read_text("utf-8").strip()))
        except Exception:
            pass
    for port in candidates:
        if _api_alive(port):
            state["api_port"] = port
            save_state(state)
            return port
    die("GPMLogin Local API unreachable on %s. Is GPMLogin running?" % candidates)


def api(path, body=None, timeout=240, strict=True):
    """Call the Local API. strict=True returns payload["data"] and dies on
    success=false; strict=False returns the whole payload for the caller to judge."""
    url = "http://127.0.0.1:%d/api/v1%s" % (api_port(), path)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        die("HTTP %s on %s: %s" % (e.code, path, e.read().decode("utf-8", "replace")[:400]))
    except Exception as e:
        die("%s on %s: %s" % (type(e).__name__, path, e))
    if not strict:
        return payload
    if not payload.get("success"):
        die("GPMLogin: %s" % payload.get("message"))
    return payload.get("data")

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def find_profile(ident):
    """Resolve a profile by id, exact name, or unique substring of the name."""
    if UUID_RE.match(ident):
        return api("/profiles/" + ident)
    q = urllib.parse.quote(ident)
    items = (api("/profiles?page_size=500&search=" + q) or {}).get("data") or []
    if not items:
        items = (api("/profiles?page_size=500") or {}).get("data") or []
    low = ident.lower()
    exact = [i for i in items if (i.get("name") or "").lower() == low]
    partial = [i for i in items if low in (i.get("name") or "").lower()]
    pool = exact or partial
    if not pool:
        die("no profile matching %r - list them with: profiles" % ident)
    if len(pool) > 1:
        die("%r matches %d profiles:\n%s" % (ident, len(pool), "\n".join(
            "  %s  %s" % (i["id"], i.get("name")) for i in pool[:15])))
    return pool[0]


def cmd_profiles(a):
    path = "/profiles?page=%d&page_size=%d&sort=%d" % (a.page, a.page_size, a.sort)
    if a.search:
        path += "&search=" + urllib.parse.quote(a.search)
    data = api(path) or {}
    groups = {}
    for g in (api("/groups?page_size=500") or {}).get("data") or []:
        groups[g["id"]] = g.get("name")
    rows = data.get("data") or []
    out("page %s/%s  total %s" % (data.get("current_page"), data.get("last_page"),
                                  data.get("total")))
    for p in rows:
        proxy = p.get("raw_proxy") or "-"
        out("%s  %-38s  group=%-14s  %s %s  proxy=%s" % (
            p["id"], (p.get("name") or "")[:38],
            (groups.get(p.get("group_id")) or p.get("group_id") or "-")[:14],
            (p.get("browser") or {}).get("name"),
            (p.get("browser") or {}).get("version"), proxy))
    if not rows:
        out("(no profiles)")


def cmd_profile(a):
    out(json.dumps(find_profile(a.profile), indent=2, ensure_ascii=False))


def cmd_groups(a):
    for g in (api("/groups?page_size=500") or {}).get("data") or []:
        out("%s  %s" % (g["id"], g.get("name")))

def cmd_create(a):
    body = {"name": a.name}
    if a.group:
        body["group_id"] = find_group_id(a.group)
    if a.proxy is not None:
        body["raw_proxy"] = a.proxy
    if a.browser_version:
        body["browser_version"] = a.browser_version
    if a.os_type:
        body["os_type"] = a.os_type
    if a.note:
        body["note"] = a.note
    if a.startup_urls:
        body["startup_urls"] = a.startup_urls
    data = api("/profiles/create", body)
    out("created %s  %s" % (data.get("id"), data.get("name")))


def find_group_id(ident):
    if UUID_RE.match(ident):
        return ident
    for g in (api("/groups?page_size=500") or {}).get("data") or []:
        if (g.get("name") or "").lower() == ident.lower():
            return g["id"]
    die("no group named %r - list them with: groups" % ident)


def cmd_delete(a):
    prof = find_profile(a.profile)
    mode = "hard" if a.hard else "soft"
    path = "/profiles/delete/%s?mode=%s" % (prof["id"], mode)
    for attempt in range(3):
        res = api(path, strict=False)
        if res.get("success"):
            out("deleted (%s) %s  %s" % (mode, prof["id"], prof.get("name")))
            return
        # "0/1" means the files are still locked by a browser that is shutting down.
        if str(res.get("message", "")).startswith("0/") and attempt < 2:
            time.sleep(4)
            continue
        die("GPMLogin: %s" % res.get("message"))


def cmd_versions(a):
    data = api("/browsers/versions") or {}
    for engine, versions in data.items():
        out("%s: %s" % (engine, ", ".join(versions)))


def cmd_start(a):
    prof = find_profile(a.profile)
    query = []
    if a.port:
        query.append("remote_debugging_port=%d" % a.port)
    if a.window_size:
        query.append("window_size=" + urllib.parse.quote(a.window_size))
    if a.window_pos:
        query.append("window_pos=" + urllib.parse.quote(a.window_pos))
    if a.scale:
        query.append("window_scale=%s" % a.scale)
    if a.skip_proxy_check:
        query.append("skip_proxy_check=true")
    if a.args:
        query.append("addition_args=" + urllib.parse.quote(a.args))
    path = "/profiles/start/" + prof["id"]
    if query:
        path += "?" + "&".join(query)
    data = api(path) or {}
    port = data.get("remote_debugging_port")
    if not port:
        die("start returned no remote_debugging_port: %s" % json.dumps(data))
    state = load_state()
    state.update({"profile_id": prof["id"], "profile_name": prof.get("name"),
                  "cdp_port": port, "ws": data.get("websocket_debugging_url")})
    state.pop("tab", None)
    save_state(state)
    out("started  %s  (%s)" % (prof.get("name"), prof["id"]))
    out("cdp      http://127.0.0.1:%s" % port)
    out("ws       %s" % data.get("websocket_debugging_url"))
    out("driver   %s" % data.get("driver_path"))
    if a.url:
        _goto(a.url, a.timeout)

def cmd_stop(a):
    if a.profile:
        prof = find_profile(a.profile)
        pid, pname = prof["id"], prof.get("name")
    else:
        state = load_state()
        pid, pname = state.get("profile_id"), state.get("profile_name")
        if not pid:
            die("no active session - pass a profile name/id")
    res = api("/profiles/stop/" + pid, strict=False)
    state = load_state()
    if state.get("profile_id") == pid:
        for k in ("cdp_port", "ws", "tab"):
            state.pop(k, None)
        save_state(state)
    if res.get("success"):
        out("stopped %s (%s)" % (pname, pid))
    elif str(res.get("message")) == "OK":
        # The API answers success=false, message=OK when nothing was running.
        out("already closed: %s (%s)" % (pname, pid))
    else:
        die("GPMLogin: %s" % res.get("message"))


def cmd_status(a):
    state = load_state()
    if not state.get("cdp_port"):
        out("no active browser session (run: start <profile>)")
        return
    out("profile  %s  (%s)" % (state.get("profile_name"), state.get("profile_id")))
    out("cdp      http://127.0.0.1:%s" % state.get("cdp_port"))
    try:
        with page_ctx() as (page, pages, _):
            out("tabs     %d, active #%d" % (len(pages), pages.index(page)))
            out("url      %s" % page.url)
            out("title    %s" % page.title())
    except SystemExit:
        raise
    except Exception as e:
        out("connect  FAILED (%s) - browser likely closed; run start again" % e)


@contextmanager
def page_ctx(tab=None, timeout_ms=30000):
    """Connect to the running browser over CDP and yield the active page.

    Never calls browser.close(): that would tear down the user's browser.
    Exiting sync_playwright() only detaches the CDP client.
    """
    state = load_state()
    if not state.get("cdp_port"):
        die("no browser session - run: start <profile>")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        die("playwright not installed - run: pip install playwright")
    with sync_playwright() as pw:
        endpoint = "http://127.0.0.1:%s" % state["cdp_port"]
        try:
            browser = pw.chromium.connect_over_cdp(endpoint, timeout=25000)
        except Exception as first:
            if not state.get("ws"):
                die("cannot attach to %s: %s\nBrowser closed? run: start <profile>"
                    % (endpoint, first))
            browser = pw.chromium.connect_over_cdp(state["ws"], timeout=25000)
        contexts = browser.contexts
        if not contexts:
            die("browser has no context - is this a Firefox profile? CDP needs Chromium")
        pages = [pg for ctx in contexts for pg in ctx.pages]
        if not pages:
            pages = [contexts[0].new_page()]
        page = pages[_pick_tab(pages, tab if tab is not None else state.get("tab"))]
        page.set_default_timeout(timeout_ms)
        try:
            yield page, pages, state
        finally:
            # A tab command sets _tab_manual to keep its own choice.
            try:
                if not state.pop("_tab_manual", False):
                    state["tab"] = pages.index(page)
                save_state(state)
            except Exception:
                pass


def _pick_tab(pages, tab):
    if isinstance(tab, int) and 0 <= tab < len(pages):
        return tab
    for i, pg in enumerate(pages):
        try:
            if pg.evaluate("document.visibilityState") == "visible":
                return i
        except Exception:
            pass
    return len(pages) - 1

SNAPSHOT_JS = r"""
() => {
  const SEL = 'a,button,input,select,textarea,summary,[role=button],[role=link],'
    + '[role=tab],[role=checkbox],[role=radio],[role=switch],[role=menuitem],'
    + '[role=option],[role=combobox],[role=textbox],[contenteditable=""],'
    + '[contenteditable="true"],[onclick]';
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none'
      && parseFloat(s.opacity || '1') > 0.05;
  };
  const label = (el) => {
    let t = el.getAttribute('aria-label') || el.getAttribute('placeholder')
      || el.getAttribute('title') || el.getAttribute('alt') || '';
    if (!t && el.tagName === 'INPUT' && el.labels && el.labels[0])
      t = el.labels[0].innerText || '';
    if (!t) t = (el.innerText || el.value || el.getAttribute('name') || '');
    return String(t).replace(/\s+/g, ' ').trim().slice(0, 100);
  };
  const kind = (el) => {
    const r = el.getAttribute('role');
    if (r) return r;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'input') return 'input:' + (el.getAttribute('type') || 'text');
    if (tag === 'textarea') return 'textarea';
    if (tag === 'select') return 'select';
    if (tag === 'button') return 'button';
    return tag;
  };
  document.querySelectorAll('[data-gpm-ref]').forEach(
    (e) => e.removeAttribute('data-gpm-ref'));
  const items = [];
  let n = 0;
  for (const el of document.querySelectorAll(SEL)) {
    if (!visible(el)) continue;
    if (++n > 250) break;
    const ref = 'e' + n;
    el.setAttribute('data-gpm-ref', ref);
    const rec = { ref: ref, kind: kind(el), name: label(el) };
    const href = el.getAttribute('href');
    if (href) rec.href = String(href).slice(0, 120);
    if (el.disabled) rec.disabled = true;
    if (el.checked) rec.checked = true;
    if (el.tagName === 'SELECT')
      rec.options = [...el.options].slice(0, 20).map((o) => o.value);
    items.push(rec);
  }
  return {
    url: location.href, title: document.title, items: items,
    text: (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n')
  };
}
"""


def cmd_snapshot(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, pages, _):
        if a.load:
            try:
                page.wait_for_load_state("networkidle", timeout=a.timeout * 1000)
            except Exception:
                pass
        snap = page.evaluate(SNAPSHOT_JS)
        if a.json:
            out(json.dumps(snap, indent=2, ensure_ascii=False))
            return
        out("url    %s" % snap["url"])
        out("title  %s" % snap["title"])
        out("tabs   %d (active #%d)" % (len(pages), pages.index(page)))
        out("")
        out("-- interactive elements (click/fill by ref) --")
        for it in snap["items"]:
            extra = ""
            if it.get("href"):
                extra += "  href=%s" % it["href"]
            if it.get("options"):
                extra += "  options=%s" % ",".join(it["options"])
            if it.get("disabled"):
                extra += "  [disabled]"
            if it.get("checked"):
                extra += "  [checked]"
            out("[%s] %-14s %s%s" % (it["ref"], it["kind"], it["name"], extra))
        if not snap["items"]:
            out("(none found - content may be inside an iframe; try: eval / text)")
        out("")
        out("-- page text --")
        text = snap["text"] or ""
        limit = a.max_text
        out(text[:limit] + ("\n...[truncated %d chars]" % (len(text) - limit)
                            if len(text) > limit else ""))

REF_RE = re.compile(r"^e\d+$")


def resolve(page, target):
    """Turn a ref (e12), CSS, text=..., or xpath into a Playwright locator."""
    if REF_RE.match(target):
        loc = page.locator("[data-gpm-ref='%s']" % target)
        if loc.count() == 0:
            die("ref %s is stale (page changed) - run: snapshot" % target)
        return loc.first
    return page.locator(target).first


def _goto(url, timeout):
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        url = "https://" + url
    with page_ctx(None, timeout * 1000) as (page, _, __):
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        out("%s  |  %s" % (page.url, page.title()))


def cmd_goto(a):
    _goto(a.url, a.timeout)


def cmd_click(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        loc = resolve(page, a.target)
        loc.scroll_into_view_if_needed(timeout=5000)
        if a.double:
            loc.dblclick(timeout=a.timeout * 1000)
        else:
            loc.click(timeout=a.timeout * 1000, button=a.button)
        page.wait_for_timeout(a.settle)
        out("clicked %s -> %s" % (a.target, page.url))


def cmd_fill(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        loc = resolve(page, a.target)
        loc.scroll_into_view_if_needed(timeout=5000)
        if a.slow:
            loc.click()
            loc.fill("")
            loc.press_sequentially(a.value, delay=a.delay)
        else:
            loc.fill(a.value)
        if a.enter:
            loc.press("Enter")
        page.wait_for_timeout(a.settle)
        out("filled %s" % a.target)


def cmd_press(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        if a.target:
            resolve(page, a.target).press(a.key)
        else:
            page.keyboard.press(a.key)
        page.wait_for_timeout(a.settle)
        out("pressed %s" % a.key)


def cmd_select(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        resolve(page, a.target).select_option(a.value)
        out("selected %s in %s" % (a.value, a.target))


def cmd_check(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        loc = resolve(page, a.target)
        loc.uncheck() if a.off else loc.check()
        out("%s %s" % ("unchecked" if a.off else "checked", a.target))


def cmd_hover(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        resolve(page, a.target).hover()
        page.wait_for_timeout(a.settle)
        out("hovered %s" % a.target)


def cmd_upload(a):
    paths = [str(Path(p).resolve()) for p in a.files]
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        resolve(page, a.target).set_input_files(paths)
        out("uploaded %s" % ", ".join(paths))

def cmd_text(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        if a.target:
            body = resolve(page, a.target).inner_text()
        else:
            body = page.evaluate("() => document.body ? document.body.innerText : ''")
        out(body[:a.max_text])


def cmd_html(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        html = resolve(page, a.target).inner_html() if a.target else page.content()
        out(html[:a.max_text])


def cmd_eval(a):
    script = a.script
    if a.file:
        script = Path(a.file).read_text("utf-8")
    if not re.match(r"^\s*(\(|function|async)", script):
        script = "() => { %s }" % script if ";" in script or "return" in script \
            else "() => (%s)" % script
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        result = page.evaluate(script)
        out(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_screenshot(a):
    path = Path(a.path) if a.path else (
        STATE_FILE.parent / ("shot-%s.png" % time.strftime("%H%M%S")))
    path.parent.mkdir(parents=True, exist_ok=True)
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        if a.target:
            resolve(page, a.target).screenshot(path=str(path))
        else:
            page.screenshot(path=str(path), full_page=a.full)
        out(str(path))


def cmd_wait(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        if a.ms:
            page.wait_for_timeout(a.ms)
            out("waited %dms" % a.ms)
        elif a.text:
            # innerText already excludes hidden nodes, so this waits for text the
            # user can actually see (get_by_text().first can latch onto a hidden match).
            page.wait_for_function(
                "t => (document.body ? document.body.innerText : '')"
                ".toLowerCase().includes(t)",
                arg=a.text.lower(), timeout=a.timeout * 1000)
            out("text appeared: %s" % a.text)
        elif a.selector:
            page.locator(a.selector).first.wait_for(
                state=a.state, timeout=a.timeout * 1000)
            out("selector %s: %s" % (a.state, a.selector))
        else:
            page.wait_for_load_state("networkidle", timeout=a.timeout * 1000)
            out("network idle")
        out("url %s" % page.url)


def cmd_nav(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        if a.action == "back":
            page.go_back()
        elif a.action == "forward":
            page.go_forward()
        else:
            page.reload()
        out("%s -> %s" % (a.action, page.url))


def cmd_scroll(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        if a.target:
            resolve(page, a.target).scroll_into_view_if_needed()
        else:
            page.mouse.wheel(0, a.dy)
        page.wait_for_timeout(a.settle)
        out("scrolled")

def cmd_tabs(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, pages, _):
        for i, pg in enumerate(pages):
            mark = "*" if pg is page else " "
            try:
                title = pg.title()
            except Exception:
                title = "?"
            out("%s #%d  %s  |  %s" % (mark, i, pg.url[:90], title[:60]))


def cmd_tab(a):
    with page_ctx(a.index, a.timeout * 1000) as (page, pages, _):
        page.bring_to_front()
        out("active #%d  %s" % (pages.index(page), page.url))


def cmd_newtab(a):
    with page_ctx(None, a.timeout * 1000) as (page, pages, state):
        new = page.context.new_page()
        if a.url:
            url = a.url if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", a.url) \
                else "https://" + a.url
            new.goto(url, wait_until="domcontentloaded", timeout=a.timeout * 1000)
        new.bring_to_front()
        # CDP page order is not the visual tab order, so don't pin an index:
        # clearing it makes the next command pick the front tab.
        state.pop("tab", None)
        state["_tab_manual"] = True
        out("opened %s (now the front tab; run `tabs` for indices)" % new.url)


def cmd_closetab(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, pages, state):
        url = page.url
        page.close()
        state.pop("tab", None)
        state["_tab_manual"] = True
        out("closed %s" % url)


def cmd_cookies(a):
    with page_ctx(a.tab, a.timeout * 1000) as (page, _, __):
        cookies = page.context.cookies()
    if a.values:
        out(json.dumps(cookies, indent=2, ensure_ascii=False))
        return
    out("%d cookies (names only; pass --values to include secrets)" % len(cookies))
    for c in cookies:
        out("  %-34s %s" % (c.get("name", "")[:34], c.get("domain")))

def build_parser():
    p = argparse.ArgumentParser(
        prog="gpm.py", description="GPMLogin profile + browser control")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.set_defaults(func=fn)
        return sp

    def common(sp, settle=0):
        sp.add_argument("--tab", type=int, default=None, help="tab index (see: tabs)")
        sp.add_argument("--timeout", type=int, default=30, help="seconds (default 30)")
        sp.add_argument("--settle", type=int, default=settle,
                        help="ms to wait after the action")
        return sp

    sp = add("profiles", cmd_profiles, "list profiles")
    sp.add_argument("--search")
    sp.add_argument("--page", type=int, default=1)
    sp.add_argument("--page-size", type=int, default=50)
    sp.add_argument("--sort", type=int, default=0)

    sp = add("profile", cmd_profile, "one profile as JSON (with fingerprint)")
    sp.add_argument("profile")

    add("groups", cmd_groups, "list groups")
    add("versions", cmd_versions, "list installed browser versions")

    sp = add("create", cmd_create, "create a profile")
    sp.add_argument("name")
    sp.add_argument("--group")
    sp.add_argument("--proxy", help="e.g. socks5://ip:port or ip:port:user:pass")
    sp.add_argument("--browser-version")
    sp.add_argument("--os-type", type=int, choices=[1, 2, 3, 4, 5],
                    help="1=Win 2=macIntel 3=macARM 4=Linux 5=Android")
    sp.add_argument("--startup-urls")
    sp.add_argument("--note")

    sp = add("delete", cmd_delete, "delete a profile (soft by default)")
    sp.add_argument("profile")
    sp.add_argument("--hard", action="store_true", help="permanent, wipes disk data")

    sp = add("start", cmd_start, "launch a profile's browser and attach")
    sp.add_argument("profile")
    sp.add_argument("--url", help="navigate here once open")
    sp.add_argument("--port", type=int, help="fixed remote debugging port")
    sp.add_argument("--window-size", help="w,h e.g. 1280,800")
    sp.add_argument("--window-pos", help="x,y e.g. 0,0")
    sp.add_argument("--scale", help="window scale, e.g. 0.8")
    sp.add_argument("--skip-proxy-check", action="store_true")
    sp.add_argument("--args", help="extra chromium flags, e.g. --mute-audio")
    sp.add_argument("--timeout", type=int, default=60)

    sp = add("stop", cmd_stop, "close a profile's browser")
    sp.add_argument("profile", nargs="?")

    common(add("status", cmd_status, "current session, tab count, url"))

    sp = common(add("snapshot", cmd_snapshot, "refs + text of the page (start here)"))
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--load", action="store_true", help="wait for network idle first")
    sp.add_argument("--max-text", type=int, default=4000)

    sp = common(add("goto", cmd_goto, "navigate the active tab"))
    sp.add_argument("url")

    sp = common(add("click", cmd_click, "click a ref/CSS/text= target"), settle=600)
    sp.add_argument("target")
    sp.add_argument("--double", action="store_true")
    sp.add_argument("--button", default="left", choices=["left", "right", "middle"])

    sp = common(add("fill", cmd_fill, "set an input's value"), settle=200)
    sp.add_argument("target")
    sp.add_argument("value")
    sp.add_argument("--enter", action="store_true", help="press Enter after")
    sp.add_argument("--slow", action="store_true", help="type key by key")
    sp.add_argument("--delay", type=int, default=60, help="ms between keys with --slow")

    sp = common(add("press", cmd_press, "send a key, e.g. Enter, Tab, Control+A"),
                settle=300)
    sp.add_argument("key")
    sp.add_argument("--target", help="focus this element first")

    sp = common(add("select", cmd_select, "pick a <select> option"))
    sp.add_argument("target")
    sp.add_argument("value")

    sp = common(add("check", cmd_check, "check/uncheck a checkbox"))
    sp.add_argument("target")
    sp.add_argument("--off", action="store_true")

    sp = common(add("hover", cmd_hover, "hover an element"), settle=400)
    sp.add_argument("target")

    sp = common(add("upload", cmd_upload, "set files on a file input"))
    sp.add_argument("target")
    sp.add_argument("files", nargs="+")

    sp = common(add("text", cmd_text, "visible text of page or element"))
    sp.add_argument("target", nargs="?")
    sp.add_argument("--max-text", type=int, default=15000)

    sp = common(add("html", cmd_html, "HTML of page or element"))
    sp.add_argument("target", nargs="?")
    sp.add_argument("--max-text", type=int, default=15000)

    sp = common(add("eval", cmd_eval, "run JS in the page and print the result"))
    sp.add_argument("script", nargs="?", default="")
    sp.add_argument("--file", help="read the JS from a file instead")

    sp = common(add("screenshot", cmd_screenshot, "PNG of page or element"))
    sp.add_argument("path", nargs="?")
    sp.add_argument("--target")
    sp.add_argument("--full", action="store_true", help="full scrollable page")

    sp = common(add("wait", cmd_wait, "wait for idle / selector / text / ms"))
    sp.add_argument("--selector")
    sp.add_argument("--text")
    sp.add_argument("--ms", type=int)
    sp.add_argument("--state", default="visible",
                    choices=["attached", "detached", "visible", "hidden"],
                    help="state --selector must reach (default visible)")

    sp = common(add("nav", cmd_nav, "back | forward | reload"))
    sp.add_argument("action", choices=["back", "forward", "reload"])

    sp = common(add("scroll", cmd_scroll, "wheel scroll or scroll a ref into view"),
                settle=300)
    sp.add_argument("target", nargs="?")
    sp.add_argument("--dy", type=int, default=600)

    common(add("tabs", cmd_tabs, "list tabs"))

    sp = common(add("tab", cmd_tab, "switch active tab"))
    sp.add_argument("index", type=int)

    sp = common(add("newtab", cmd_newtab, "open a new tab"))
    sp.add_argument("url", nargs="?")

    common(add("closetab", cmd_closetab, "close the active tab"))

    sp = common(add("cookies", cmd_cookies, "cookie names (or --values)"))
    sp.add_argument("--values", action="store_true")

# __APPEND2__
    return p

def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        die("interrupted", 130)
    except Exception as e:
        msg = str(e).split("\nCall log")[0].strip()
        die("%s: %s" % (type(e).__name__, msg))


if __name__ == "__main__":
    main()
