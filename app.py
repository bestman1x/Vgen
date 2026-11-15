import streamlit as st
import asyncio
import json
import logging
import os
import urllib.parse
import re
import io
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
from pypdf import PdfReader

# ============================================================
# CRITICAL FIX: Install the Chromium binary for Streamlit Cloud
# ============================================================
os.system("playwright install chromium")

# Safely import playwright-stealth
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
    page_title="Username Extractors",
    page_icon="📋",
    layout="wide"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# SHARED HELPER FUNCTIONS
# ============================================================
GITHUB_PAGES_URL = "https://bestman1x.github.io/Vgen/"

def generate_dashboard_link(usernames):
    json_str = json.dumps(usernames, ensure_ascii=False)
    encoded_data = urllib.parse.quote(json_str)
    return f"{GITHUB_PAGES_URL}#data={encoded_data}"

def show_dashboard_button(usernames):
    if not usernames:
        return
        
    final_url = generate_dashboard_link(usernames)
    st.success(f"Found {len(usernames)} unique usernames.")
    st.info("🎉 Your custom copy-list is ready! Click the button below to open it.")
    
    st.markdown(
        f"""
        <a href="{final_url}" target="_blank" style="
            display: inline-block;
            padding: 12px 24px;
            background-color: #4f8cff;
            color: white;
            text-decoration: none;
            border-radius: 8px;
            font-weight: bold;
            font-size: 16px;
            margin-top: 10px;
        ">
            🚀 Open Username Dashboard
        </a>
        """, 
        unsafe_allow_html=True
    )
    st.caption("Note: You can bookmark or share the generated link. The data is stored entirely inside the URL!")

# ============================================================
# FIVERR PDF PARSING LOGIC
# ============================================================
BLACKLIST = {
    'yes', 'no', 'price', 'duration', 'helpful', 'ongoing',
    'collaboration', 'united', 'states', 'related', 'tags',
    'anime', 'logo', 'mascot', 'illustrations', 'design',
    'maker', 'projects', 'pro', 'level', 'reviews',
}

def is_username(token):
    if not token or not (3 <= len(token) <= 25):
        return False
    if not re.match(r'^[a-z][a-z0-9_]*$', token):
        return False
    if token in BLACKLIST:
        return False
    return True

def last_token(line):
    parts = line.strip().split()
    return parts[-1] if parts else ''

def parse_fiverr_text(text):
    lines = text.split('\n')
    usernames = []
    seen = set()

    def try_add(tok):
        if is_username(tok) and tok not in seen:
            seen.add(tok)
            usernames.append(tok)

    # RULE 1: Username is usually 2 lines above "Ongoing collaboration"
    for i, line in enumerate(lines):
        if 'Ongoing collaboration' in line:
            for offset in (2, 1, 3): 
                j = i - offset
                if j >= 0:
                    tok = last_token(lines[j])
                    if is_username(tok):
                        try_add(tok)
                        break

    # RULE 2: Catch the last review (after "Duration")
    for i, line in enumerate(lines):
        if 'Duration' in line:
            for j in range(i + 1, min(i + 3, len(lines))):
                try_add(last_token(lines[j]))

    return usernames

# ============================================================
# VGEN SCRAPER LOGIC
# ============================================================
def validate_vgen_url(url):
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"): return False
        if parsed.hostname not in ("vgen.co", "www.vgen.co"): return False
        if not parsed.path.strip("/"): return False
        return True
    except:
        return False

async def run_vgen_scraper(url, start_pg, end_pg, status_text):
    browser = None
    all_usernames = []
    seen_usernames = set()

    try:
        status_text.info("Initializing browser...")
        
        if HAS_STEALTH_CLASS:
            p_manager = Stealth().use_async(async_playwright())
        else:
            p_manager = async_playwright()

        async with p_manager as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"),
                viewport={"width": 1920, "height": 1080}
            )
            page = await context.new_page()
            page.set_default_timeout(15000)

            if HAS_STEALTH_CLASS is False:
                await stealth_async(page)

            status_text.info("Opening VGen profile...")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            status_text.info("Looking for reviews...")
            review_button = page.locator("div.chipButton", has_text="reviews").first

            try:
                await review_button.wait_for(state="visible", timeout=15000)
            except PlaywrightTimeoutError:
                raise RuntimeError("Reviews button was not found.")

            await review_button.click()
            await asyncio.sleep(3)

            review_selector = "div[class*='Review__Container']"
            review_cards = page.locator(review_selector)

            try:
                await review_cards.first.wait_for(state="visible", timeout=15000)
            except PlaywrightTimeoutError:
                status_text.warning("No review cards were found.")
                return []

            current_page = 1
            
            while current_page < start_pg:
                next_button = page.locator("div[class*='controls'] button").nth(1)
                if not await next_button.is_visible() or not await next_button.is_enabled():
                    status_text.warning(f"Reached the last page before hitting Start Page {start_pg}.")
                    return []
                status_text.info(f"Skipping to page {start_pg} (Currently on {current_page})...")
                await next_button.click(timeout=5000)
                await asyncio.sleep(1.5)
                current_page += 1

            while current_page <= end_pg:
                status_text.info(f"Extracting usernames from page {current_page}...")
                cards = await review_cards.all()
                current_page_names = []

                for card in cards:
                    name = None
                    try:
                        name_el = card.locator("p.displayName")
                        if await name_el.count() > 0:
                            name = (await name_el.first.inner_text()).strip()
                        
                        if not name:
                            link_el = card.locator("a[href*='vgen.co/']")
                            if await link_el.count() > 0:
                                href = await link_el.first.get_attribute("href")
                                if href:
                                    name = href.split("vgen.co/")[-1].split("?")[0].strip("/")
                        
                        if not name:
                            details_el = card.locator("div[class*='reviewerDetails']")
                            if await details_el.count() > 0:
                                name = (await details_el.first.inner_text()).split("\n")[0].strip()

                        if name:
                            current_page_names.append(name)
                    except PlaywrightError:
                        pass

                for name in current_page_names:
                    if name not in seen_usernames:
                        seen_usernames.add(name)
                        all_usernames.append(name)

                if current_page >= end_pg:
                    break

                next_button = page.locator("div[class*='controls'] button").nth(1)
                if not await next_button.is_visible() or not await next_button.is_enabled():
                    break

                try:
                    await next_button.click(timeout=5000)
                    await asyncio.sleep(1.5)
                    current_page += 1
                except PlaywrightTimeoutError:
                    break

            status_text.success(f"Successfully extracted {len(all_usernames)} unique usernames.")
            return all_usernames

    except Exception as e:
        status_text.error(f"An unexpected error occurred: {e}")
        return None
    finally:
        if browser:
            try: await browser.close()
            except: pass

# ============================================================
# MAIN STREAMLIT UI (TABS)
# ============================================================
st.title("📋 Username Extractors")

tab_vgen, tab_fiverr = st.tabs(["🎮 VGen Scraper", "🟢 Fiverr PDF Parser"])

# --- TAB 1: VGEN ---
with tab_vgen:
    st.write("Enter a VGen profile URL and select the page range you want to extract.")
    
    url_input = st.text_input("VGen Profile URL", "https://vgen.co/")
    col1, col2 = st.columns(2)
    with col1:
        start_page = st.number_input("Start Page", min_value=1, value=1, step=1)
    with col2:
        end_page = st.number_input("End Page (Leave high to get all)", min_value=1, value=999, step=1)

    if st.button("Start VGen Scraping", type="primary"):
        if not url_input.strip():
            st.warning("Please enter a VGen profile URL.")
        elif not validate_vgen_url(url_input):
            st.error("Invalid URL. Please enter a valid VGen profile URL.")
        elif start_page > end_page:
            st.error("Start Page cannot be greater than End Page.")
        else:
            status_text = st.empty()
            with st.spinner("Scraping in progress. Please wait..."):
                usernames = asyncio.run(run_vgen_scraper(url_input.strip(), start_page, end_page, status_text))
            
            if usernames:
                show_dashboard_button(usernames)
            elif usernames is not None and len(usernames) == 0:
                st.warning("Scraping completed, but no usernames were found.")

# --- TAB 2: FIVERR ---
with tab_fiverr:
    st.write("Upload a Fiverr PDF (saved from your phone) to extract reviewer usernames.")
    st.caption("⚠️ Note: Make sure you scroll all the way down to load the reviews before printing the page to PDF!")
    
    uploaded_file = st.file_uploader("Upload Fiverr PDF", type=["pdf"])
    
    if uploaded_file is not None:
        if st.button("Extract from PDF", type="primary"):
            with st.spinner("Parsing PDF..."):
                try:
                    # Read PDF in memory
                    reader = PdfReader(uploaded_file)
                    full_text = ""
                    for page in reader.pages:
                        extracted = page.extract_text()
                        if extracted:
                            full_text += extracted + "\n"
                    
                    # Parse usernames
                    usernames = parse_fiverr_text(full_text)
                    
                    if usernames:
                        show_dashboard_button(usernames)
                    else:
                        st.warning("No usernames could be extracted from this PDF. Make sure the PDF contains the Fiverr review section.")
                        
                except Exception as e:
                    st.error(f"Failed to read PDF: {e}")
