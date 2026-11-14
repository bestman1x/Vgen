import streamlit as st
import streamlit.components.v1 as components
import asyncio
import json
import logging
import os
os.system("playwright install chromium")
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

# Safely import playwright-stealth (handles both v1 and v2 of the library)
try:
    from playwright_stealth import Stealth
    HAS_STEALTH_CLASS = True
except ImportError:
    try:
        from playwright_stealth import stealth_async
        HAS_STEALTH_CLASS = False
    except ImportError:
        HAS_STEALTH_CLASS = None

# ============================================================
# CONFIGURATION
# ============================================================
st.set_page_config(
    page_title="VGen Username Extractor",
    page_icon="📋",
    layout="wide"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# STREAMLIT INTERFACE
# ============================================================
st.title("📋 VGen Username Extractor")
st.write("Enter a VGen profile URL below. Once scraped, your interactive Username Copy List will appear.")

url_input = st.text_input("VGen Profile URL", "https://vgen.co/fafysstuff_")

# ============================================================
# URL VALIDATION
# ============================================================
def validate_vgen_url(url):
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.hostname not in ("vgen.co", "www.vgen.co"):
            return False
        if not parsed.path.strip("/"):
            return False
        return True
    except (ValueError, AttributeError):
        return False

# ============================================================
# SCRAPER
# ============================================================
async def run_scraper(url):
    browser = None
    all_usernames = []
    seen_usernames = set()
    visited_pages = set()
    status_text = st.empty()

    try:
        status_text.info("Initializing browser...")
        
        # Handle the Playwright context based on which stealth version is installed
        if HAS_STEALTH_CLASS:
            p_manager = Stealth().use_async(async_playwright())
        else:
            p_manager = async_playwright()

        async with p_manager as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36")
            )
            page = await context.new_page()
            page.set_default_timeout(15000)

            # Apply stealth if using the older stealth_async version
            if HAS_STEALTH_CLASS is False:
                await stealth_async(page)

            # ------------------------------------------------
            # NAVIGATION
            # ------------------------------------------------
            status_text.info("Opening VGen profile...")
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            if response and response.status >= 400:
                raise RuntimeError(f"VGen returned HTTP {response.status}")

            # ------------------------------------------------
            # FIND REVIEWS BUTTON
            # ------------------------------------------------
            status_text.info("Looking for reviews...")
            review_button = page.locator("div.chipButton", has_text="reviews").first

            try:
                await review_button.wait_for(state="visible", timeout=15000)
            except PlaywrightTimeoutError:
                raise RuntimeError("Reviews button was not found. The profile may be unavailable or the page structure may have changed.")

            await review_button.click()

            # ------------------------------------------------
            # WAIT FOR REVIEW CARDS
            # ------------------------------------------------
            review_selector = "div[class*='Review__Container']"
            review_cards = page.locator(review_selector)

            try:
                await review_cards.first.wait_for(state="visible", timeout=15000)
            except PlaywrightTimeoutError:
                status_text.warning("The reviews section opened, but no review cards were found.")
                return []

            # ------------------------------------------------
            # PAGINATION
            # ------------------------------------------------
            page_num = 1
            while True:
                status_text.info(f"Extracting usernames from page {page_num}...")
                cards = await review_cards.all()
                current_page_names = []

                for card in cards:
                    try:
                        name_element = card.locator("p.displayName")
                        name = (await name_element.inner_text()).strip()
                        if name:
                            current_page_names.append(name)
                    except PlaywrightError:
                        pass

                # Detect repeated pages
                page_signature = tuple(current_page_names)
                if page_signature in visited_pages:
                    logger.warning("Repeated page detected. Stopping pagination.")
                    break
                visited_pages.add(page_signature)

                # Remove duplicates
                for name in current_page_names:
                    if name not in seen_usernames:
                        seen_usernames.add(name)
                        all_usernames.append(name)

                # Find next button
                next_button = page.locator("div[class*='controls'] button").nth(1)

                if not await next_button.is_visible() or not await next_button.is_enabled():
                    break

                previous_signature = page_signature

                # Click next and wait for content change
                try:
                    await next_button.click(timeout=5000)
                    await page.wait_for_function(
                        """
                        (args) => {
                            const cards = document.querySelectorAll(args.selector);
                            const names = Array.from(cards).map(card => {
                                const el = card.querySelector("p.displayName");
                                return el ? el.innerText.trim() : "";
                            });
                            return JSON.stringify(names) !== args.previous;
                        }
                        """,
                        arg={
                            "selector": review_selector,
                            "previous": json.dumps(list(previous_signature))
                        },
                        timeout=10000
                    )
                    page_num += 1
                except PlaywrightTimeoutError:
                    logger.warning("Pagination did not produce new content. Stopping.")
                    break

            status_text.success(f"Successfully extracted {len(all_usernames)} unique usernames from {page_num} page(s).")
            return all_usernames

    except PlaywrightTimeoutError:
        status_text.error("The operation timed out. VGen may be loading slowly or its page structure may have changed.")
        logger.exception("Playwright timeout")
        return None
    except PlaywrightError as e:
        status_text.error(f"A browser error occurred: {e}")
        logger.exception("Playwright browser error")
        return None
    except Exception as e:
        status_text.error(f"An unexpected error occurred: {e}")
        logger.exception("Unexpected scraping error")
        return None
    finally:
        if browser:
            try:
                await browser.close()
            except PlaywrightError:
                pass

# ============================================================
# USERNAME DASHBOARD
# ============================================================
def generate_username_dashboard(usernames):
    # Escaping for safe JS injection
    js_array = (
        json.dumps(usernames, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )

    html_template = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Usernames — Copy List</title>
    <style>
    :root {{
        --bg: #0f1117; --card: #171a23; --card-hover: #1e2230; --text: #e6e9ef;
        --muted: #8b93a7; --accent: #4f8cff; --accent-glow: rgba(79, 140, 255, 0.35);
        --copied-bg: #22c55e; --copied-text: #04210f; --border: #262b39;
        --streak: #f59e0b; --streak-glow: rgba(245, 158, 11, 0.35);
    }}
    * {{ box-sizing: border-box; }}
    body {{
        margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        background: var(--bg); color: var(--text); padding: 24px; line-height: 1.4;
    }}
    header {{ max-width: 1100px; margin: 0 auto 16px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; }}
    h1 {{ font-size: 1.4rem; margin: 0; font-weight: 700; letter-spacing: -0.01em; }}
    .stats {{ color: var(--muted); font-size: 0.9rem; }}
    .streak-bar {{
        max-width: 1100px; margin: 0 auto 16px; display: flex; align-items: center; gap: 12px;
        padding: 12px 16px; background: var(--card); border: 1px solid var(--border); border-radius: 12px; font-size: 0.92rem;
    }}
    .streak-bar .label {{ color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 0.72rem; letter-spacing: 0.06em; }}
    .streak-bar .value {{ font-size: 1.4rem; font-weight: 800; color: var(--streak); min-width: 2ch; text-align: center; text-shadow: 0 0 12px var(--streak-glow); transition: transform 0.15s; }}
    .streak-bar .value.bump {{ transform: scale(1.25); }}
    .streak-bar .value.broken {{ color: var(--muted); text-shadow: none; }}
    .streak-bar .sub {{ color: var(--muted); font-size: 0.82rem; margin-left: auto; }}
    .toolbar {{ max-width: 1100px; margin: 0 auto 20px; display: flex; gap: 10px; flex-wrap: wrap; }}
    .toolbar input {{ flex: 1 1 240px; padding: 10px 14px; border-radius: 10px; border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 0.95rem; outline: none; transition: border-color 0.15s; }}
    .toolbar input:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }}
    .toolbar button {{ padding: 10px 16px; border-radius: 10px; border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 0.9rem; cursor: pointer; transition: background 0.15s, border-color 0.15s; }}
    .toolbar button:hover {{ background: var(--card-hover); border-color: var(--accent); }}
    .grid {{ max-width: 1100px; margin: 0 auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }}
    .user-card {{ display: flex; align-items: center; gap: 10px; background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 8px 10px; transition: background 0.15s, border-color 0.15s; }}
    .user-card:hover {{ background: var(--card-hover); }}
    .user-card .sn {{ flex: 0 0 auto; min-width: 34px; height: 26px; display: inline-flex; align-items: center; justify-content: center; padding: 0 8px; border-radius: 7px; background: rgba(79, 140, 255, 0.1); border: 1px solid rgba(79, 140, 255, 0.25); color: var(--accent); font-size: 0.72rem; font-weight: 700; font-variant-numeric: tabular-nums; }}
    .user-card .name {{ font-size: 0.92rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; padding: 4px 8px; border-radius: 7px; transition: background 0.2s, color 0.2s, box-shadow 0.2s; }}
    .user-card .name.copied-name {{ background: var(--copied-bg); color: var(--copied-text); font-weight: 700; box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.35); animation: textPulse 0.6s ease-out; }}
    @keyframes textPulse {{ 0% {{ box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.6); }} 100% {{ box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.35); }} }}
    .user-card .copy-btn {{ flex: 0 0 auto; border: 1px solid var(--border); background: transparent; color: var(--muted); border-radius: 8px; padding: 6px 10px; font-size: 0.78rem; font-weight: 600; cursor: pointer; transition: all 0.15s; }}
    .user-card .copy-btn:hover {{ color: var(--text); border-color: var(--accent); background: rgba(79, 140, 255, 0.08); }}
    .toast {{ position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(80px); background: var(--card); border: 1px solid var(--copied-bg); color: var(--text); padding: 10px 18px; border-radius: 10px; font-size: 0.9rem; opacity: 0; transition: transform 0.25s, opacity 0.25s; pointer-events: none; z-index: 100; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }}
    .toast.show {{ transform: translateX(-50%) translateY(0); opacity: 1; }}
    @media (max-width: 520px) {{ body {{ padding: 14px; }} .grid {{ grid-template-columns: 1fr; }} .streak-bar {{ flex-wrap: wrap; }} }}
    </style>
    </head>
    <body>
    <header>
        <h1>📋 Usernames</h1>
        <div class="stats"><span id="count">0</span> usernames · <span id="shown">0</span> shown</div>
    </header>
    <div class="streak-bar">
        <span class="label">🔥 Streak in a row</span>
        <span class="value" id="streakValue">0</span>
        <span class="sub" id="streakSub">Copy a username to start a streak</span>
    </div>
    <div class="toolbar">
        <input id="search" type="text" placeholder="Search usernames…" autocomplete="off" />
        <button id="clear-highlight">Clear highlight &amp; streak</button>
    </div>
    <div class="grid" id="grid"></div>
    <div class="toast" id="toast">Copied!</div>

    <script>
    const usernames = {js_array};
    const KEY_LAST = "lastCopiedUsername_v3";
    const KEY_STREAK = "copyStreak_v3";

    const grid = document.getElementById("grid");
    const searchInput = document.getElementById("search");
    const toast = document.getElementById("toast");
    const countEl = document.getElementById("count");
    const shownEl = document.getElementById("shown");
    const clearBtn = document.getElementById("clear-highlight");
    const streakValEl = document.getElementById("streakValue");
    const streakSubEl = document.getElementById("streakSub");

    let lastCopiedName = null;
    let streak = 0;
    let toastTimer = null;

    function render(filter = "") {{
        const q = filter.trim().toLowerCase();
        const fragment = document.createDocumentFragment();
        let shown = 0;

        usernames.forEach((name, index) => {{
            if (q && !name.toLowerCase().includes(q)) return;

            const card = document.createElement("div");
            card.className = "user-card";
            card.dataset.index = index;
            card.dataset.name = name;

            const sn = document.createElement("span");
            sn.className = "sn";
            sn.textContent = index + 1;

            const span = document.createElement("span");
            span.className = "name";
            span.textContent = name;
            span.title = name;

            const btn = document.createElement("button");
            btn.className = "copy-btn";
            btn.textContent = "Copy";
            btn.addEventListener("click", () => copyUsername(index, name, span, btn));

            card.appendChild(sn);
            card.appendChild(span);
            card.appendChild(btn);
            fragment.appendChild(card);
            shown++;
        }});

        grid.replaceChildren(fragment);
        shownEl.textContent = shown;

        if (lastCopiedName !== null) {{
            const card = Array.from(grid.querySelectorAll(".user-card")).find(el => el.dataset.name === lastCopiedName);
            if (card) {{
                const nameSpan = card.querySelector(".name");
                if (nameSpan) nameSpan.classList.add("copied-name");
            }}
        }}
    }}

    async function copyUsername(index, name, nameSpan, btn) {{
        let ok = false;
        if (navigator.clipboard && window.isSecureContext) {{
            try {{ await navigator.clipboard.writeText(name); ok = true; }} catch (e) {{ console.warn(e); }}
        }}
        
        if (!ok) {{
            let ta = null;
            try {{
                ta = document.createElement("textarea");
                ta.value = name;
                ta.setAttribute("readonly", "");
                ta.style.position = "fixed";
                ta.style.top = "-9999px";
                document.body.appendChild(ta);
                ta.select();
                ta.setSelectionRange(0, ta.value.length);
                ok = document.execCommand("copy");
            }} catch (e) {{ ok = false; }}
            finally {{ if (ta && ta.parentNode) ta.parentNode.removeChild(ta); }}
        }}

        if (!ok) {{
            showToast("Copy failed — long-press or right-click to copy");
            return;
        }}

        const isSameAsLast = name === lastCopiedName;
        if (isSameAsLast) {{
            streak = 1;
            streakValEl.classList.add("broken");
            setTimeout(() => streakValEl.classList.remove("broken"), 500);
        }} else {{
            streak = (streak || 0) + 1;
            streakValEl.classList.remove("broken");
        }}

        streakValEl.textContent = streak;
        streakValEl.classList.remove("bump");
        void streakValEl.offsetWidth;
        streakValEl.classList.add("bump");

        streakSubEl.textContent = streak === 1 ? "1 copy — keep going!" : `${{streak}} unique copies in a row 🔥`;

        lastCopiedName = name;
        try {{
            localStorage.setItem(KEY_LAST, name);
            localStorage.setItem(KEY_STREAK, String(streak));
        }} catch (e) {{ console.warn(e); }}

        grid.querySelectorAll(".name.copied-name").forEach(el => el.classList.remove("copied-name"));
        nameSpan.classList.add("copied-name");

        btn.textContent = "Copied!";
        setTimeout(() => {{ btn.textContent = "Copy"; }}, 1200);
        showToast(`Copied: ${{name}} · streak ${{streak}}`);
    }}

    function showToast(msg) {{
        toast.textContent = msg;
        toast.classList.add("show");
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => toast.classList.remove("show"), 1800);
    }}

    function restoreAndScroll() {{
        let saved = null;
        let savedStreak = 0;
        try {{
            saved = localStorage.getItem(KEY_LAST);
            savedStreak = parseInt(localStorage.getItem(KEY_STREAK) || "0", 10) || 0;
        }} catch (e) {{ console.warn(e); }}

        streak = savedStreak;
        streakValEl.textContent = streak;
        streakSubEl.textContent = streak === 0 ? "Copy a username to start a streak" : `${{streak}} unique cop${{streak === 1 ? "y" : "ies"}} in a row 🔥`;

        if (!saved || !usernames.includes(saved)) return;
        lastCopiedName = saved;

        requestAnimationFrame(() => {{
            const card = Array.from(grid.querySelectorAll(".user-card")).find(el => el.dataset.name === saved);
            if (!card) return;
            const nameSpan = card.querySelector(".name");
            if (nameSpan) nameSpan.classList.add("copied-name");
            card.scrollIntoView({{ behavior: "smooth", block: "center" }});
        }});
    }}

    searchInput.addEventListener("input", (e) => render(e.target.value));

    clearBtn.addEventListener("click", () => {{
        try {{
            localStorage.removeItem(KEY_LAST);
            localStorage.removeItem(KEY_STREAK);
        }} catch (e) {{ console.warn(e); }}
        
        lastCopiedName = null;
        streak = 0;
        streakValEl.textContent = "0";
        streakValEl.classList.add("broken");
        streakSubEl.textContent = "Copy a username to start a streak";
        grid.querySelectorAll(".name.copied-name").forEach(el => el.classList.remove("copied-name"));
        showToast("Highlight & streak cleared");
    }});

    countEl.textContent = usernames.length;
    render();
    window.addEventListener("load", restoreAndScroll);
    if (document.readyState === "complete") restoreAndScroll();
    </script>
    </body>
    </html>
    """
    return html_template

# ============================================================
# START SCRAPING
# ============================================================
if st.button("Start Scraping", type="primary"):
    if not url_input.strip():
        st.warning("Please enter a VGen profile URL.")
    elif not validate_vgen_url(url_input):
        st.error("Invalid URL. Please enter a valid VGen profile URL.")
    else:
        try:
            with st.spinner("Scraping in progress. Please wait..."):
                usernames = asyncio.run(run_scraper(url_input.strip()))

            if usernames is None:
                st.error("Scraping failed. Please try again.")
            elif len(usernames) == 0:
                st.warning("Scraping completed, but no usernames were found.")
            else:
                st.success(f"Found {len(usernames)} unique usernames.")
                html_template = generate_username_dashboard(usernames)
                components.html(html_template, height=1000, scrolling=True)

        except Exception:
            st.error("Something went wrong while running the scraper.")
            logger.exception("Streamlit execution error")
