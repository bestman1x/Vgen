import streamlit as st
import asyncio
import json
import logging
import os
import urllib.parse
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

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
st.write("Enter a VGen profile URL and select the page range you want to extract.")

url_input = st.text_input("VGen Profile URL", "https://vgen.co/fafysstuff_")

col1, col2 = st.columns(2)
with col1:
    start_page = st.number_input("Start Page", min_value=1, value=1, step=1)
with col2:
    end_page = st.number_input("End Page (Leave high to get all)", min_value=1, value=999, step=1)

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
async def run_scraper(url, start_pg, end_pg):
    browser = None
    all_usernames = []
    seen_usernames = set()
    status_text = st.empty()

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
            
            # Skip pages if start_pg is greater than 1
            while current_page < start_pg:
                next_button = page.locator("div[class*='controls'] button").nth(1)
                if not await next_button.is_visible() or not await next_button.is_enabled():
                    status_text.warning(f"Reached the last page before hitting Start Page {start_pg}.")
                    return []
                
                status_text.info(f"Skipping to page {start_pg} (Currently on {current_page})...")
                await next_button.click(timeout=5000)
                await asyncio.sleep(1.5)
                current_page += 1

            # Start scraping from start_pg
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

                # Check if we've reached the end_page limit
                if current_page >= end_pg:
                    break

                # Go to next page
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
        logger.exception("Unexpected scraping error")
        return None
    finally:
        if browser:
            try:
                await browser.close()
            except:
                pass

# ============================================================
# START SCRAPING
# ============================================================
if st.button("Start Scraping", type="primary"):
    if not url_input.strip():
        st.warning("Please enter a VGen profile URL.")
    elif not validate_vgen_url(url_input):
        st.error("Invalid URL. Please enter a valid VGen profile URL.")
    elif start_page > end_page:
        st.error("Start Page cannot be greater than End Page.")
    else:
        try:
            with st.spinner("Scraping in progress. Please wait..."):
                usernames = asyncio.run(run_scraper(url_input.strip(), start_page, end_page))

            if usernames is None:
                st.error("Scraping failed. Please try again.")
            elif len(usernames) == 0:
                st.warning("Scraping completed, but no usernames were found. Did you select a valid page range?")
            else:
                st.success(f"Found {len(usernames)} unique usernames.")
                
                # Generate the URL-encoded data string
                json_str = json.dumps(usernames, ensure_ascii=False)
                encoded_data = urllib.parse.quote(json_str)
                
                # IMPORTANT: REPLACE THIS WITH YOUR ACTUAL GITHUB PAGES URL
                GITHUB_PAGES_URL = "https://bestman1x.github.io/Vgen/" 
                
                final_url = f"{GITHUB_PAGES_URL}#data={encoded_data}"
                
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

        except Exception:
            st.error("Something went wrong while running the scraper.")
            logger.exception("Streamlit execution error")
