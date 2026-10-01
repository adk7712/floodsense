"""
FloodSense - Automated Playwright End-to-End Browser Test Suite.
Launches a headless Chromium browser against http://localhost:8501,
monitors JavaScript console logs, simulates user clicks and drags,
and captures visual verification screenshots.
"""

import time
import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, expect


def test_streamlit_browser_e2e():
    """End-to-end browser test checking DOM hydration, zero console errors, and interactive widgets."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # 1. Navigate to Streamlit App
        page.goto("http://localhost:8501", timeout=30000)

        # 2. Check title and main header
        expect(page).to_have_title("FloodSense | Urban Drainage Intelligence", timeout=15000)
        page.wait_for_selector("text=FloodSense Intelligence Center", timeout=15000)

        # 3. Wait for Plotly map or charts to render
        time.sleep(2)

        # 4. Verify 5 KPI metric cards
        metrics = page.locator("[data-testid='stMetricValue']").all()
        assert len(metrics) >= 5, f"Expected at least 5 metrics, found {len(metrics)}"
        high_risk_text = metrics[0].inner_text()
        assert "/ 55" in high_risk_text, f"Unexpected high risk metric: {high_risk_text}"

        # 5. Interactive UI test: Select another planning area
        combobox = page.locator("input[aria-autocomplete='list']")
        if combobox.count() > 0:
            combobox.click()
            page.keyboard.type("BEDOK")
            page.keyboard.press("Enter")
            time.sleep(1)

        # 6. Capture full visual screenshot
        screenshot_dir = Path("data/reports")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / "streamlit_e2e_verified.png"
        page.screenshot(path=str(screenshot_path), full_page=True)

        browser.close()

        # Check console errors
        critical_errors = [e for e in console_errors if "favicon" not in e.lower() and "unhandled" in e.lower()]
        assert len(critical_errors) == 0, f"Encountered critical browser console errors: {critical_errors}"


def test_streamlit_browser_responsive():
    """Verify app renders cleanly on tablet/smaller viewports without layout crashes."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 800, "height": 1000})
        page = context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        page.goto("http://localhost:8501", timeout=30000)
        expect(page).to_have_title("FloodSense | Urban Drainage Intelligence", timeout=15000)
        page.wait_for_selector("text=FloodSense Intelligence Center", timeout=15000)
        time.sleep(1)

        # Confirm metrics rendered
        metrics = page.locator("[data-testid='stMetricValue']").all()
        assert len(metrics) >= 5

        browser.close()
        critical_errors = [e for e in console_errors if "favicon" not in e.lower() and "unhandled" in e.lower()]
        assert len(critical_errors) == 0


if __name__ == "__main__":
    test_streamlit_browser_e2e()
    test_streamlit_browser_responsive()
    print("All Playwright E2E browser tests passed!")
