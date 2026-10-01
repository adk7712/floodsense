"""
FloodSense - Streamlit App Automated In-Process Stress Test Suite.
Uses st.testing.v1.AppTest to simulate user interactions, widget changes,
mode toggles, replay timeline scrubbing, and zone selections with zero browser overhead.
"""

from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).parent.parent / "src" / "app" / "streamlit_app.py")


def test_streamlit_app_default_render():
    """Verify that the default app view loads smoothly without errors or unhandled exceptions."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=15)
    assert not at.exception
    assert len(at.title) >= 1
    assert "FloodSense" in at.title[0].value


def test_streamlit_app_mode_toggle():
    """Stress-test toggling between Replay Storm and Live Feed modes."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=15)
    assert not at.exception

    seg = None
    if hasattr(at, "segmented_control") and at.segmented_control:
        seg = at.segmented_control[0]
    elif hasattr(at.sidebar, "button_group") and at.sidebar.button_group:
        seg = at.sidebar.button_group[0]
    elif hasattr(at, "button_group") and at.button_group:
        seg = at.button_group[0]

    if seg:
        # Switch to Live Feed
        seg.set_value("Live Feed")
        at.run(timeout=15)
        assert not at.exception

        # Switch back to Replay Storm
        seg.set_value("Replay Storm")
        at.run(timeout=15)
        assert not at.exception


def test_streamlit_app_replay_slider_scrubbing():
    """Stress-test scrubbing through multiple timesteps of the April 2021 storm."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=15)
    assert not at.exception

    if at.slider:
        slider = at.slider[0]
        # Test step 0 (beginning of storm)
        slider.set_value(0)
        at.run(timeout=15)
        assert not at.exception

        # Test peak storm step (step 26)
        slider.set_value(26)
        at.run(timeout=15)
        assert not at.exception

        # Test storm end (step 59)
        slider.set_value(59)
        at.run(timeout=15)
        assert not at.exception


def test_streamlit_app_all_zones_selection():
    """Stress-test selecting various planning zones from the dropdown."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=15)
    assert not at.exception

    sample_zones = ["BUKIT TIMAH", "BEDOK", "JURONG WEST", "PUNGGOL", "DOWNTOWN CORE"]
    if at.selectbox:
        box = at.selectbox[0]
        for zone in sample_zones:
            box.set_value(zone)
            at.run(timeout=10)
            assert not at.exception
