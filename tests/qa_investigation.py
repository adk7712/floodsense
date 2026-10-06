"""
Comprehensive QA Investigation Script for FloodSense Streamlit App.
Tests Task 1, 2, and 3:
- Cold start & Live Feed failure behavior
- Mode switching to Replay Storm
- Preset storm buttons and state desync
- Time slider scrubbing latency & stability
- Planning area dropdown cycling (BUKIT TIMAH, BEDOK, JURONG EAST, CENTRAL WATER CATCHMENT, CHANGI, etc.)
- Date picker edge cases
- Responsive viewports: 1440x900, 1024x768, 390x844
- Console errors & DOM inspection
- Screenshot captures
"""

import sys
import time
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

SCREENSHOT_DIR = Path("tests/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

def run_qa_tests():
    results = {}
    console_logs = []
    page_errors = []

    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=True)
        
        # ---------------------------------------------------------
        # SCENARIO 1: Cold Start / Live Feed Mode Check
        # ---------------------------------------------------------
        print("\n=== SCENARIO 1: COLD START & LIVE FEED MODE ===")
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        
        page.on("console", lambda msg: console_logs.append({"type": msg.type, "text": msg.text, "context": "cold_start"}))
        page.on("pageerror", lambda err: page_errors.append({"error": str(err), "context": "cold_start"}))

        t0 = time.time()
        page.goto("http://localhost:8501", timeout=30000)
        page.wait_for_selector("[data-testid='stAppViewContainer']", timeout=20000)
        time.sleep(3) # wait for streamlit rerun / load
        load_duration = time.time() - t0

        cold_start_screenshot = SCREENSHOT_DIR / "qa_cold_start_live.png"
        page.screenshot(path=str(cold_start_screenshot), full_page=True)
        print(f"Cold start loaded in {load_duration:.2f}s. Screenshot: {cold_start_screenshot}")

        # Check for error banners, buttons, stopped state
        error_alerts = page.locator("[data-testid='stAlert']").all_text_contents()
        buttons = page.locator("button").all_text_contents()
        has_stop = len(page.locator("[data-testid='stMetric']").all()) == 0
        
        results["cold_start"] = {
            "load_duration_s": load_duration,
            "alerts": error_alerts,
            "buttons": buttons,
            "halted_at_st_stop": has_stop,
            "title": page.title()
        }
        print("Cold start results:", json.dumps(results["cold_start"], indent=2))

        # Check what fallback options are visible to the user on this cold start screen
        retry_button_present = any("Retry" in b for b in buttons)
        replay_button_present = any("Replay" in b for b in buttons)
        print(f"Retry button present: {retry_button_present}, Replay button in main area: {replay_button_present}")

        # ---------------------------------------------------------
        # SCENARIO 2: Mode Switching to Replay Storm
        # ---------------------------------------------------------
        print("\n=== SCENARIO 2: SWITCH TO REPLAY STORM ===")
        # Look for segmented control button for 'Replay Storm'
        replay_toggle = page.locator("button[data-testid='stButtonGroupButton']:has-text('Replay Storm')")
        if replay_toggle.count() > 0:
            print("Found 'Replay Storm' toggle in sidebar. Clicking...")
            t_switch_start = time.time()
            replay_toggle.click()
            time.sleep(3) # Wait for Streamlit rerun and rendering
            switch_duration = time.time() - t_switch_start
            
            # Wait for metrics to appear
            page.wait_for_selector("[data-testid='stMetric']", timeout=15000)
            metrics = page.locator("[data-testid='stMetricValue']").all_text_contents()
            labels = page.locator("[data-testid='stMetricLabel']").all_text_contents()
            
            replay_screenshot = SCREENSHOT_DIR / "qa_switched_replay_storm.png"
            page.screenshot(path=str(replay_screenshot), full_page=True)
            print(f"Switched to Replay Storm in {switch_duration:.2f}s. Screenshot: {replay_screenshot}")
            print(f"Metrics ({len(metrics)}): {list(zip(labels, metrics))}")
            
            results["mode_switch"] = {
                "switch_duration_s": switch_duration,
                "metrics_count": len(metrics),
                "metrics": dict(zip(labels, metrics))
            }
        else:
            print("ERROR: Could not find 'Replay Storm' button!")
            results["mode_switch"] = {"error": "Replay Storm button not found"}

        # ---------------------------------------------------------
        # SCENARIO 3: Preset Storm Buttons Testing & Desync Check
        # ---------------------------------------------------------
        print("\n=== SCENARIO 3: PRESET STORM BUTTONS ===")
        # Get all storm buttons in sidebar
        storm_buttons = page.locator("section[data-testid='stSidebar'] button[kind='secondary'], section[data-testid='stSidebar'] button[kind='primary']").all()
        storm_button_texts = [b.inner_text().strip() for b in storm_buttons if "·" in b.inner_text()]
        print(f"Found {len(storm_button_texts)} storm preset buttons: {storm_button_texts}")

        preset_results = []
        for btn_text in storm_button_texts:
            print(f"\nClicking preset storm: '{btn_text}'...")
            btn = page.locator(f"section[data-testid='stSidebar'] button:has-text('{btn_text[:11]}')").first
            t_click = time.time()
            btn.click()
            time.sleep(2) # wait for rerun
            click_latency = time.time() - t_click
            
            # Read current date input value, current slider value, and status line
            date_input = page.locator("section[data-testid='stSidebar'] [data-baseweb='input'] input").first.input_value()
            slider = page.locator("section[data-testid='stSidebar'] [data-testid='stSelectSlider']")
            slider_text = slider.inner_text() if slider.count() > 0 else "NO SLIDER"
            
            # Check metrics
            metrics = page.locator("[data-testid='stMetricValue']").all_text_contents()
            labels = page.locator("[data-testid='stMetricLabel']").all_text_contents()
            status_line = page.locator("text=Timestamp:").first.inner_text() if page.locator("text=Timestamp:").count() > 0 else "NO TIMESTAMP"

            safe_name = btn_text[:11].replace(" ", "_")
            ss_path = SCREENSHOT_DIR / f"qa_preset_{safe_name}.png"
            page.screenshot(path=str(ss_path), full_page=True)

            preset_results.append({
                "button": btn_text,
                "latency_s": click_latency,
                "date_input": date_input,
                "slider_summary": slider_text[:100],
                "status_line": status_line,
                "metrics": dict(zip(labels, metrics)),
                "screenshot": str(ss_path)
            })
            print(f"Preset '{btn_text}' -> Date: {date_input}, Status: {status_line}, Metrics count: {len(metrics)}")

        results["presets"] = preset_results

        # ---------------------------------------------------------
        # SCENARIO 4: Time Slider Scrubbing on 17 Apr 2021
        # ---------------------------------------------------------
        print("\n=== SCENARIO 4: TIME SLIDER SCRUBBING ON 17 APR 2021 ===")
        # First ensure we are on 17 Apr 2021
        btn_17apr = page.locator("section[data-testid='stSidebar'] button:has-text('17 Apr 2021')").first
        if btn_17apr.count() > 0:
            btn_17apr.click()
            time.sleep(2)

        # Let's inspect the slider options on 17 Apr 2021
        # In streamlit select_slider, the slider uses keyboard arrow keys or clicking track.
        # Let's test scrubbing through slider using keyboard Left / Right keys on the slider thumb.
        slider_thumb = page.locator("section[data-testid='stSidebar'] [data-testid='stSelectSlider'] div[role='slider']")
        scrubbing_data = []
        if slider_thumb.count() > 0:
            slider_thumb.click()
            time.sleep(0.5)

            # Move to earliest position by pressing 'Home' or multiple 'PageUp'/'ArrowLeft'
            page.keyboard.press("Home")
            time.sleep(1)
            
            # Read all available options or step through
            print("Stepping through slider using ArrowRight...")
            steps_recorded = 0
            for step in range(85): # 11:00 to 18:00 has ~84 5-min intervals
                t_step_start = time.time()
                page.keyboard.press("ArrowRight")
                time.sleep(0.3)
                
                # Check for timestamp
                status_loc = page.locator("text=Timestamp:")
                cur_ts = status_loc.inner_text() if status_loc.count() > 0 else ""
                
                # Check for metrics
                metrics = page.locator("[data-testid='stMetricValue']").all_text_contents()
                max_rain = metrics[2] if len(metrics) > 2 else "N/A"
                high_risk = metrics[0] if len(metrics) > 0 else "N/A"
                
                dt = time.time() - t_step_start
                steps_recorded += 1
                
                # Capture specific critical timestamps: 12:45 and 13:35
                if "12:45" in cur_ts:
                    ss_peak_1245 = SCREENSHOT_DIR / "qa_peak_1245.png"
                    page.screenshot(path=str(ss_peak_1245), full_page=True)
                    print(f"Captured PEAK 12:45 screenshot: {ss_peak_1245}")
                elif "13:35" in cur_ts:
                    ss_peak_1335 = SCREENSHOT_DIR / "qa_peak_1335.png"
                    page.screenshot(path=str(ss_peak_1335), full_page=True)
                    print(f"Captured PEAK 13:35 screenshot: {ss_peak_1335}")

                if step % 10 == 0 or "12:45" in cur_ts or "13:35" in cur_ts:
                    scrubbing_data.append({
                        "step": step,
                        "timestamp": cur_ts,
                        "high_risk": high_risk,
                        "max_rain": max_rain,
                        "latency_s": dt
                    })
                    print(f"Step {step}: {cur_ts} | High Risk: {high_risk} | Max Rain: {max_rain} ({dt:.2f}s)")
            
            results["slider_scrubbing"] = {
                "steps_tested": steps_recorded,
                "samples": scrubbing_data
            }
        else:
            print("ERROR: Slider thumb not found!")
            results["slider_scrubbing"] = {"error": "Slider thumb not found"}

        # ---------------------------------------------------------
        # SCENARIO 5: Planning Area Dropdown Cycling
        # ---------------------------------------------------------
        print("\n=== SCENARIO 5: PLANNING AREA DROPDOWN CYCLING ===")
        # Ensure we test BUKIT TIMAH, BEDOK, JURONG EAST, CENTRAL WATER CATCHMENT, CHANGI, TUAS, PUNGGOL, NOVENA
        test_zones = [
            "BUKIT TIMAH",
            "BEDOK",
            "JURONG EAST",
            "CENTRAL WATER CATCHMENT",
            "CHANGI",
            "TUAS",
            "PUNGGOL",
            "NOVENA",
            "QUEENSTOWN",
            "MARINA SOUTH"
        ]
        
        zone_results = []
        combobox = page.locator("section[data-testid='stSidebar'] input[aria-autocomplete='list']")
        
        for zone in test_zones:
            t_z = time.time()
            if combobox.count() > 0:
                combobox.click()
                # Clear existing text
                page.keyboard.press("Meta+A")
                page.keyboard.press("Backspace")
                page.keyboard.type(zone)
                time.sleep(0.3)
                page.keyboard.press("Enter")
                time.sleep(1) # Wait for Streamlit update
                
                # Check zone diagnostic card
                zone_diag = page.locator("div[data-testid='stColumn']:nth-of-type(2)").first.inner_text()
                
                # Check gauge or rarity status
                rarity_text = page.locator("text=STORM RARITY PERCENTILE").locator("..").inner_text() if page.locator("text=STORM RARITY PERCENTILE").count() > 0 else "NO RARITY"
                
                # Check for NaN or null in zone diagnostic text
                has_nan = "nan" in zone_diag.lower() or "none" in zone_diag.lower() or "null" in zone_diag.lower()
                
                lat_z = time.time() - t_z
                safe_zone = zone.replace(" ", "_")
                ss_z = SCREENSHOT_DIR / f"qa_zone_{safe_zone}.png"
                page.screenshot(path=str(ss_z), full_page=True)
                
                zone_results.append({
                    "zone": zone,
                    "latency_s": lat_z,
                    "has_nan": has_nan,
                    "rarity_summary": rarity_text.replace("\n", " | "),
                    "screenshot": str(ss_z)
                })
                print(f"Zone {zone}: Latency={lat_z:.2f}s, Has NaN={has_nan}, Rarity={rarity_text.replace(chr(10), ' | ')}")

        results["zone_cycling"] = zone_results

        # ---------------------------------------------------------
        # SCENARIO 6: Edge-Case Date Picker Testing
        # ---------------------------------------------------------
        print("\n=== SCENARIO 6: EDGE CASE DATE PICKER ===")
        # Test dates:
        # 1. 01/01/2017 (Boundary start)
        # 2. 29/02/2020 (Leap year)
        # 3. Completely dry day (e.g. 15/02/2019 or dry Feb 2019)
        # 4. Out of range / Missing day
        date_input_loc = page.locator("section[data-testid='stSidebar'] [data-baseweb='input'] input").first
        
        edge_dates = [
            ("29/02/2020", "Leap year day"),
            ("01/01/2017", "Store boundary start"),
            ("15/02/2019", "Typically dry February day"),
            ("01/01/2010", "Out of bounds date (pre-store)")
        ]
        
        edge_results = []
        for date_str, desc in edge_dates:
            print(f"\nTesting edge date: {date_str} ({desc})...")
            t_ed = time.time()
            date_input_loc.click()
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(date_str)
            page.keyboard.press("Enter")
            time.sleep(2)
            
            alerts = page.locator("[data-testid='stAlert']").all_text_contents()
            metrics = page.locator("[data-testid='stMetricValue']").all_text_contents()
            safe_d = date_str.replace("/", "_")
            ss_ed = SCREENSHOT_DIR / f"qa_edge_date_{safe_d}.png"
            page.screenshot(path=str(ss_ed), full_page=True)
            
            edge_results.append({
                "date": date_str,
                "desc": desc,
                "latency_s": time.time() - t_ed,
                "alerts": alerts,
                "metrics_count": len(metrics),
                "metrics": metrics,
                "screenshot": str(ss_ed)
            })
            print(f"Date {date_str} -> Alerts: {alerts}, Metrics count: {len(metrics)}")

        results["edge_dates"] = edge_results

        # ---------------------------------------------------------
        # SCENARIO 7: Responsive Layouts
        # ---------------------------------------------------------
        print("\n=== SCENARIO 7: RESPONSIVE LAYOUT TESTING ===")
        viewports = [
            ("desktop_1440x900", 1440, 900),
            ("laptop_1024x768", 1024, 768),
            ("mobile_390x844", 390, 844)
        ]
        
        responsive_results = []
        for vp_name, w, h in viewports:
            print(f"\nTesting viewport: {vp_name} ({w}x{h})...")
            resp_context = browser.new_context(viewport={"width": w, "height": h})
            resp_page = resp_context.new_page()
            
            resp_page.goto("http://localhost:8501", timeout=30000)
            time.sleep(2)
            
            # Switch to Replay Storm if in live mode
            replay_btn = resp_page.locator("button[data-testid='stButtonGroupButton']:has-text('Replay Storm')")
            if replay_btn.count() > 0:
                replay_btn.click()
                time.sleep(2)
            else:
                # If sidebar is collapsed (e.g. mobile), check if hamburger is visible
                sidebar_toggle = resp_page.locator("[data-testid='stSidebarCollapseButton']")
                if sidebar_toggle.count() > 0:
                    print("Opening collapsed sidebar on mobile/tablet...")
                    sidebar_toggle.click()
                    time.sleep(1)
                    replay_btn = resp_page.locator("button[data-testid='stButtonGroupButton']:has-text('Replay Storm')")
                    if replay_btn.count() > 0:
                        replay_btn.click()
                        time.sleep(2)

            ss_vp = SCREENSHOT_DIR / f"qa_viewport_{vp_name}.png"
            resp_page.screenshot(path=str(ss_vp), full_page=True)
            
            # Check for horizontal scroll / overflow
            scroll_width = resp_page.evaluate("() => document.documentElement.scrollWidth")
            client_width = resp_page.evaluate("() => document.documentElement.clientWidth")
            has_h_overflow = scroll_width > client_width
            
            # Check metric card layout
            metric_boxes = resp_page.locator("[data-testid='stMetric']").all()
            
            responsive_results.append({
                "viewport": vp_name,
                "width": w,
                "height": h,
                "scroll_width": scroll_width,
                "client_width": client_width,
                "horizontal_overflow": has_h_overflow,
                "metrics_rendered": len(metric_boxes),
                "screenshot": str(ss_vp)
            })
            print(f"Viewport {vp_name}: ScrollWidth={scroll_width}, ClientWidth={client_width}, Overflow={has_h_overflow}, Metrics={len(metric_boxes)}")
            resp_context.close()

        results["responsive"] = responsive_results
        
        # Collect console and page errors
        results["console_logs"] = console_logs
        results["page_errors"] = page_errors

        context.close()
        browser.close()

    with open("tests/qa_test_report_raw.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nSaved raw QA results to tests/qa_test_report_raw.json")

if __name__ == "__main__":
    run_qa_tests()
