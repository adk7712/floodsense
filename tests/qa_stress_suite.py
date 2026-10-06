"""
Comprehensive Playwright QA Stress Test Suite for FloodSense.
Executes detailed testing for Tasks 1, 2, and 3:
- Cold start & Live Feed failure behavior
- Mode switching to Replay Storm
- Preset storm buttons and state desync
- Time slider scrubbing latency & stability (11:00 to 18:00 on 17 Apr 2021)
- Planning area dropdown cycling (BUKIT TIMAH, BEDOK, JURONG EAST, CENTRAL WATER CATCHMENT, CHANGI, etc.)
- Date picker edge cases (leap year, boundary date, dry day, out of range)
- Responsive viewports: 1440x900, 1024x768, 390x844
- Console errors, warnings, & DOM inspection
- Screenshot captures of critical states (peak 12:45, peak 13:35, failure states, viewports)
"""

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SCREENSHOT_DIR = Path("tests/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_FILE = Path("tests/qa_stress_report.json")


def run_stress_suite():
    report = {
        "task1_mode_switching_cold_start": {},
        "task2_stress_controls": {},
        "task3_console_and_dom": {},
        "screenshots": [],
    }

    console_errors = []
    console_warnings = []
    page_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # =========================================================================
        # TASK 1: Cold Start & Live Feed Fallback Analysis
        # =========================================================================
        print("\n" + "=" * 60)
        print("TASK 1: COLD START & MODE SWITCHING ANALYSIS")
        print("=" * 60)

        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        def on_console(msg):
            if msg.type == "error":
                console_errors.append({"text": msg.text, "location": msg.location})
            elif msg.type == "warning":
                console_warnings.append({"text": msg.text, "location": msg.location})

        page.on("console", on_console)
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        t0 = time.time()
        page.goto("http://localhost:8501", timeout=30000)
        page.wait_for_selector("[data-testid='stAppViewContainer']", timeout=20000)
        time.sleep(2.5)  # Wait for initial Streamlit run
        cold_load_time = time.time() - t0

        ss_cold = SCREENSHOT_DIR / "qa_task1_cold_start.png"
        page.screenshot(path=str(ss_cold), full_page=True)
        report["screenshots"].append(str(ss_cold))

        # Check DOM state during cold start
        alerts = page.locator("[data-testid='stAlert']").all_text_contents()
        has_metrics = len(page.locator("[data-testid='stMetric']").all()) > 0
        has_map = len(page.locator(".js-plotly-plot").all()) > 0
        retry_buttons = page.locator("button:has-text('Retry')").all_text_contents()
        replay_cta_in_main = page.locator(
            "[data-testid='stMain'] button:has-text('Replay')"
        ).all_text_contents()

        print(f"Cold Start Page Loaded in {cold_load_time:.2f}s")
        print(f"Alerts found: {alerts}")
        print(f"Metrics rendered: {has_metrics}, Map rendered: {has_map}")
        print(f"Retry CTA in main: {retry_buttons}")
        print(f"Replay fallback button in main: {replay_cta_in_main}")

        report["task1_mode_switching_cold_start"]["cold_start"] = {
            "load_time_seconds": round(cold_load_time, 2),
            "alerts": alerts,
            "halted_at_st_stop": not has_metrics,
            "has_metrics": has_metrics,
            "has_map": has_map,
            "retry_button_present": len(retry_buttons) > 0,
            "fallback_replay_button_in_main_view": len(replay_cta_in_main) > 0,
            "analysis": (
                "When live data.gov.sg API fails/rate-limits on cold start, the app renders raw HTTP error "
                "traceback in st.error and halts immediately with st.stop(). No metrics, map, or zone cards "
                "are rendered. Critically, there is NO direct action button in the main alert area to switch "
                "to Replay mode; only a 'Retry now' button is rendered, leaving the user stranded unless they "
                "discover the sidebar control."
            ),
        }

        # -------------------------------------------------------------------------
        # Switch to Replay Storm mode
        # -------------------------------------------------------------------------
        print("\n--- Switching to 'Replay Storm' ---")
        t_switch_start = time.time()
        main_replay_btn = page.locator("button:has-text('Switch to Replay Storm')")
        if main_replay_btn.count() > 0:
            print("Found fallback CTA button in main view! Clicking fallback CTA...")
            main_replay_btn.first.click()
        else:
            replay_toggle = page.locator("button:has-text('Replay Storm')").first
            replay_toggle.click()
        time.sleep(3)  # Wait for rerun and render
        switch_latency = time.time() - t_switch_start

        # Verify Replay Storm components
        metrics_after_switch = page.locator("[data-testid='stMetricValue']").all_text_contents()
        metric_labels_after_switch = page.locator(
            "[data-testid='stMetricLabel']"
        ).all_text_contents()
        map_rendered = len(page.locator(".js-plotly-plot").all()) > 0
        status_badge = (
            page.locator("text=Replay:").first.inner_text()
            if page.locator("text=Replay:").count() > 0
            else ""
        )

        ss_switched = SCREENSHOT_DIR / "qa_task1_switched_to_replay.png"
        page.screenshot(path=str(ss_switched), full_page=True)
        report["screenshots"].append(str(ss_switched))

        print(f"Switch to Replay completed in {switch_latency:.2f}s")
        print(
            f"Metrics: {dict(zip(metric_labels_after_switch, metrics_after_switch, strict=False))}"
        )
        print(f"Map rendered: {map_rendered}, Status badge: {status_badge}")

        report["task1_mode_switching_cold_start"]["mode_switch_to_replay"] = {
            "latency_seconds": round(switch_latency, 2),
            "metrics_count": len(metrics_after_switch),
            "metrics": dict(zip(metric_labels_after_switch, metrics_after_switch, strict=False)),
            "map_rendered": map_rendered,
            "status_badge": status_badge,
        }

        # -------------------------------------------------------------------------
        # Test All Preset Storm Buttons
        # -------------------------------------------------------------------------
        print("\n--- Testing Preset Storm Buttons ---")
        presets_to_test = [
            ("17 Apr 2021", "2021-04-17"),
            ("08 Jan 2018", "2018-01-08"),
            ("23 Jun 2020", "2020-06-23"),
            ("22 Nov 2024", "2024-11-22"),
            ("13 Apr 2025", "2025-04-13"),
            ("04 Dec 2025", "2025-12-04"),
        ]

        preset_results = []
        for storm_label, expected_iso in presets_to_test:
            print(f"Testing preset button: '{storm_label}'...")
            btn = page.locator(
                f"section[data-testid='stSidebar'] button:has-text('{storm_label}')"
            ).first
            assert btn.count() > 0, f"Button {storm_label} not found!"

            t_click = time.time()
            btn.click()
            time.sleep(2.5)  # Allow Streamlit to compute and re-render
            click_latency = time.time() - t_click

            # Check date input value
            date_val = page.locator("[data-testid='stDateInput'] input").first.input_value()

            # Check slider presence and value
            slider = page.locator("[data-testid='stSlider']").first
            slider_present = slider.count() > 0
            slider_text = slider.inner_text().splitlines() if slider_present else []

            # Check metrics
            metrics = page.locator("[data-testid='stMetricValue']").all_text_contents()
            labels = page.locator("[data-testid='stMetricLabel']").all_text_contents()
            status_line = (
                page.locator("text=Timestamp:").first.inner_text()
                if page.locator("text=Timestamp:").count() > 0
                else ""
            )

            # Check if any error banner popped up
            alerts = page.locator("[data-testid='stAlert']").all_text_contents()

            safe_name = storm_label.replace(" ", "_")
            ss_preset = SCREENSHOT_DIR / f"qa_preset_{safe_name}.png"
            page.screenshot(path=str(ss_preset), full_page=True)
            report["screenshots"].append(str(ss_preset))

            res = {
                "preset": storm_label,
                "expected_iso": expected_iso,
                "actual_date_input": date_val,
                "date_match": expected_iso == date_val,
                "latency_seconds": round(click_latency, 2),
                "slider_present": slider_present,
                "slider_summary": slider_text[:2],
                "status_timestamp": status_line,
                "metrics": dict(zip(labels, metrics, strict=False)),
                "alerts": alerts,
                "screenshot": str(ss_preset),
            }
            preset_results.append(res)
            print(
                f"  -> Date: {date_val} (match={res['date_match']}), Latency: {click_latency:.2f}s, Max Rain: {metrics[2] if len(metrics) > 2 else 'N/A'}"
            )

        report["task1_mode_switching_cold_start"]["preset_buttons"] = preset_results

        # =========================================================================
        # TASK 2: Stress Test Interactive Controls
        # =========================================================================
        print("\n" + "=" * 60)
        print("TASK 2: STRESS TEST INTERACTIVE CONTROLS")
        print("=" * 60)

        # -------------------------------------------------------------------------
        # 1. Scrubbing Time Slider on 17 Apr 2021 (11:00 to 18:00)
        # -------------------------------------------------------------------------
        print("\n--- 1. Scrubbing Time Slider on 17 Apr 2021 (11:00 to 18:00) ---")
        # Ensure we are back on 17 Apr 2021
        btn_17apr = page.locator(
            "section[data-testid='stSidebar'] button:has-text('17 Apr 2021')"
        ).first
        btn_17apr.click()
        time.sleep(2)

        # Focus the range input of the select slider
        slider_input = page.locator("[data-testid='stSlider'] input[type='range']").first
        assert slider_input.count() > 0, "Slider input[type='range'] not found!"

        slider_input.focus()
        time.sleep(0.3)
        # Move to 00:00 start (value 0)
        page.keyboard.press("Home")
        time.sleep(1)

        # Advance to 11:00 (step 132)
        print("Navigating to 11:00 (step 132)...")
        for _ in range(132):
            page.keyboard.press("ArrowRight")
        time.sleep(1.5)

        initial_ts = page.locator("text=Timestamp:").first.inner_text()
        print(f"Positioned at: {initial_ts}")

        # Now scrub step-by-step from 11:00 to 18:00 (85 steps)
        scrubbing_steps = []
        latencies = []
        flicker_count = 0

        print("Scrubbing through all 85 steps (11:00 to 18:00)...")
        for step_idx in range(85):
            t_step = time.time()
            page.keyboard.press("ArrowRight")

            # Wait for Streamlit to register update
            time.sleep(0.18)
            step_lat = time.time() - t_step
            latencies.append(step_lat)

            # Read current status timestamp
            status_text = (
                page.locator("text=Timestamp:").first.inner_text()
                if page.locator("text=Timestamp:").count() > 0
                else ""
            )
            metrics = page.locator("[data-testid='stMetricValue']").all_text_contents()

            # Check for visual flicker / blank screen
            metrics_count = len(metrics)
            if metrics_count < 5:
                flicker_count += 1

            # Capture critical peak states
            if "12:45" in status_text and not any(
                s.get("captured") == "12:45" for s in scrubbing_steps
            ):
                time.sleep(0.5)  # allow complete Plotly render
                ss_1245 = SCREENSHOT_DIR / "qa_peak_1245_17apr2021.png"
                page.screenshot(path=str(ss_1245), full_page=True)
                report["screenshots"].append(str(ss_1245))
                print(f"Captured PEAK 12:45 screenshot: {ss_1245}")
            elif "13:35" in status_text and not any(
                s.get("captured") == "13:35" for s in scrubbing_steps
            ):
                time.sleep(0.5)  # allow complete Plotly render
                ss_1335 = SCREENSHOT_DIR / "qa_peak_1335_17apr2021.png"
                page.screenshot(path=str(ss_1335), full_page=True)
                report["screenshots"].append(str(ss_1335))
                print(f"Captured PEAK 13:35 screenshot: {ss_1335}")

            if step_idx % 10 == 0 or "12:45" in status_text or "13:35" in status_text:
                scrubbing_steps.append(
                    {
                        "step_index": step_idx,
                        "timestamp": status_text,
                        "metrics": metrics,
                        "latency_s": round(step_lat, 3),
                        "captured": "12:45"
                        if "12:45" in status_text
                        else ("13:35" if "13:35" in status_text else None),
                    }
                )
                print(
                    f"  Step {step_idx:02d} | {status_text} | Metrics: {metrics[:3]} | Latency: {step_lat:.3f}s"
                )

        avg_lat = sum(latencies) / len(latencies) if latencies else 0
        p95_lat = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

        print(
            f"Scrubbing Complete. Steps: {len(latencies)}, Avg Latency: {avg_lat:.3f}s, P95: {p95_lat:.3f}s, Flickers: {flicker_count}"
        )

        report["task2_stress_controls"]["slider_scrubbing"] = {
            "total_steps": len(latencies),
            "avg_latency_seconds": round(avg_lat, 3),
            "min_latency_seconds": round(min(latencies), 3),
            "max_latency_seconds": round(max(latencies), 3),
            "p95_latency_seconds": round(p95_lat, 3),
            "flicker_or_empty_render_count": flicker_count,
            "sampled_steps": scrubbing_steps,
        }

        # ---------------------------------------------------------
        # 2. Cycle Through Multiple Planning Areas
        # ---------------------------------------------------------
        print("\n--- 2. Planning Area Dropdown Cycling ---")
        zones_to_test = [
            "BUKIT TIMAH",
            "BEDOK",
            "JURONG EAST",
            "CENTRAL WATER CATCHMENT",
            "CHANGI",
            "TUAS",
            "PUNGGOL",
            "NOVENA",
            "QUEENSTOWN",
            "MARINA SOUTH",
            "SIMPANG",
            "WESTERN ISLANDS",
        ]

        zone_results = []
        combobox = page.locator(
            "section[data-testid='stSidebar'] input[aria-autocomplete='list']"
        ).first

        for zone in zones_to_test:
            print(f"Testing planning area: {zone}...")
            t_zone = time.time()
            combobox.click()
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            page.keyboard.type(zone)
            time.sleep(0.3)
            page.keyboard.press("Enter")
            time.sleep(1.2)  # Allow Streamlit rerun
            zone_lat = time.time() - t_zone

            # Check Zone Diagnostic Card
            zone_card = page.locator("div[data-testid='stColumn']:nth-of-type(2)").first
            zone_card_text = zone_card.inner_text()

            # Check Gauge Value
            gauge_svg = page.locator(".st-key-rarity_gauge svg")
            gauge_present = gauge_svg.count() > 0

            # Check for NaN / null / undefined / division by zero
            has_nan = any(
                token in zone_card_text.lower()
                for token in ["nan", "none", "null", "undefined", "inf"]
            )

            # Check gauge numbers
            gauge_text = page.locator(".st-key-rarity_gauge").first.inner_text()

            safe_z = zone.replace(" ", "_")
            ss_zone = SCREENSHOT_DIR / f"qa_zone_{safe_z}.png"
            page.screenshot(path=str(ss_zone), full_page=True)
            report["screenshots"].append(str(ss_zone))

            z_res = {
                "zone": zone,
                "latency_seconds": round(zone_lat, 2),
                "has_nan_or_error": has_nan,
                "gauge_present": gauge_present,
                "gauge_text_sample": gauge_text[:100].replace("\n", " "),
                "zone_diagnostic_sample": zone_card_text[:150].replace("\n", " | "),
                "screenshot": str(ss_zone),
            }
            zone_results.append(z_res)
            print(
                f"  -> {zone}: NaN/Error={has_nan}, Gauge={gauge_present}, Latency={zone_lat:.2f}s"
            )

        report["task2_stress_controls"]["zone_cycling"] = zone_results

        # ---------------------------------------------------------
        # 3. Edge-Case Date Picker Testing
        # ---------------------------------------------------------
        print("\n--- 3. Edge-Case Date Picker Testing ---")
        di_input = page.locator("[data-testid='stDateInput'] input").first

        edge_cases = [
            ("2020-02-29", "Leap year date (29 Feb 2020)"),
            ("2017-01-01", "Store boundary start (1 Jan 2017)"),
            ("2019-02-15", "Completely dry day (no rain)"),
            ("2010-01-01", "Out of bounds date (pre-store era, 0 readings)"),
        ]

        edge_date_results = []
        for iso_date, desc in edge_cases:
            print(f"Testing edge date: {iso_date} ({desc})...")
            t_edge = time.time()
            di_input.fill(iso_date)
            di_input.press("Enter")
            time.sleep(2.5)  # Allow Streamlit rerun
            edge_lat = time.time() - t_edge

            alerts = page.locator("[data-testid='stAlert']").all_text_contents()
            metrics = page.locator("[data-testid='stMetricValue']").all_text_contents()
            metric_count = len(metrics)

            safe_ed = iso_date.replace("-", "_")
            ss_ed = SCREENSHOT_DIR / f"qa_edge_date_{safe_ed}.png"
            page.screenshot(path=str(ss_ed), full_page=True)
            report["screenshots"].append(str(ss_ed))

            ed_res = {
                "iso_date": iso_date,
                "description": desc,
                "latency_seconds": round(edge_lat, 2),
                "metrics_rendered": metric_count,
                "metrics": metrics,
                "alerts": alerts,
                "screenshot": str(ss_ed),
            }
            edge_date_results.append(ed_res)
            print(f"  -> Date {iso_date}: Rendered {metric_count} metrics. Alerts: {alerts}")

        report["task2_stress_controls"]["edge_dates"] = edge_date_results

        # ---------------------------------------------------------
        # 4. Responsive Viewports: Desktop vs Laptop/Tablet vs Mobile
        # ---------------------------------------------------------
        print("\n--- 4. Responsive Viewport Testing ---")
        viewports = [
            ("desktop_1440x900", 1440, 900),
            ("laptop_1024x768", 1024, 768),
            ("mobile_390x844", 390, 844),
        ]

        responsive_results = []
        for vp_name, w, h in viewports:
            print(f"Testing viewport: {vp_name} ({w}x{h})...")
            resp_ctx = browser.new_context(viewport={"width": w, "height": h})
            resp_page = resp_ctx.new_page()

            resp_page.goto("http://localhost:8501", timeout=30000)
            time.sleep(2)

            # Switch to Replay Storm
            sidebar = resp_page.locator("section[data-testid='stSidebar']")
            if sidebar.count() > 0 and sidebar.get_attribute("aria-expanded") == "false":
                expand_btn = resp_page.locator("[data-testid='stExpandSidebarButton']")
                if expand_btn.count() > 0:
                    expand_btn.click()
                    time.sleep(1)

            replay_btn = resp_page.locator("button:has-text('Replay Storm')").first
            if replay_btn.count() > 0:
                replay_btn.click()
                time.sleep(3)

            # Capture viewport screenshot
            ss_vp = SCREENSHOT_DIR / f"qa_layout_{vp_name}.png"
            resp_page.screenshot(path=str(ss_vp), full_page=True)
            report["screenshots"].append(str(ss_vp))

            # Inspect DOM layout & overflow
            scroll_width = resp_page.evaluate("() => document.documentElement.scrollWidth")
            client_width = resp_page.evaluate("() => document.documentElement.clientWidth")
            horizontal_overflow = scroll_width > client_width

            metrics = resp_page.locator("[data-testid='stMetric']").all()
            plotly_charts = resp_page.locator(".js-plotly-plot").all()

            vp_res = {
                "viewport": vp_name,
                "width": w,
                "height": h,
                "scroll_width": scroll_width,
                "client_width": client_width,
                "has_horizontal_overflow": horizontal_overflow,
                "overflow_pixels": scroll_width - client_width,
                "metrics_rendered": len(metrics),
                "charts_rendered": len(plotly_charts),
                "screenshot": str(ss_vp),
            }
            responsive_results.append(vp_res)
            print(
                f"  -> {vp_name}: Overflow={horizontal_overflow} ({scroll_width} vs {client_width}px), Metrics={len(metrics)}, Charts={len(plotly_charts)}"
            )
            resp_ctx.close()

        report["task2_stress_controls"]["responsive_layouts"] = responsive_results

        # =========================================================================
        # TASK 3: Inspect Browser Console & DOM
        # =========================================================================
        print("\n" + "=" * 60)
        print("TASK 3: CONSOLE & DOM INSPECTION")
        print("=" * 60)

        # Deduplicate and categorize console errors and warnings
        unique_errors = list({e["text"]: e for e in console_errors}.values())
        unique_warnings = list({w["text"]: w for w in console_warnings}.values())
        print(f"Total console error entries: {len(console_errors)}, Unique: {len(unique_errors)}")
        print(f"Total console warnings: {len(console_warnings)}, Unique: {len(unique_warnings)}")
        print(f"Page errors: {page_errors}")

        report["task3_console_and_dom"] = {
            "console_errors_count": len(console_errors),
            "unique_console_errors": unique_errors,
            "console_warnings_count": len(console_warnings),
            "unique_console_warnings": unique_warnings[:10],
            "page_errors": page_errors,
        }

        context.close()
        browser.close()

    # Save final JSON report
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 60)
    print(f"QA STRESS TEST COMPLETED. Report saved to {REPORT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    run_stress_suite()
