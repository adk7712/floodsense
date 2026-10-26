"""
FloodSense - Plain-language text shared by the app and the Telegram bot.

One place for what each risk tier tells people to do, so the app's zone panel and the bot's
messages never drift apart.
"""

ADVICE = {
    "High": (
        "Avoid underpasses and low-lying roads. Do not drive or walk through floodwater. "
        "Check PUB's alerts for what is happening near you."
    ),
    "Moderate": "Be ready: keep an eye on the weather and check PUB's channels for updates.",
    "Low": (
        "No action suggested. Carry on as normal, and check PUB's channels if the weather changes."
    ),
}
DISCLAIMER = "FloodSense complements, and does not replace, PUB's flood alerts."
# COPY CHANGE FOR REVIEW (T1): honesty line shown with the disclaimer (Telegram footer, Resident view).
HONESTY_LINE = "FloodSense is not an official warning; alerts can be late or missed; follow PUB."

# --- Telegram subscriptions (per-area private alerts) --------------------------------------------
MAX_AREAS_PER_CHAT = 5
CHANNEL_LINK = "https://t.me/floodsense"
CHANNEL_TEXT = (
    "FloodSense has a Telegram channel that posts alerts for all areas with Moderate or High "
    f"flash-flood risk: {CHANNEL_LINK}"
)
PRIVACY_NOTE = (
    "We store only your chat ID and the areas you pick, to send you alerts. "
    "Send /unsubscribe all to delete them."
)
HELP_TEXT = (
    "FloodSense sends you a private message when the flash-flood risk in a Singapore planning "
    "area you pick rises to Moderate or High.\n\n"
    "Commands:\n"
    "/subscribe <area>: get alerts for an area, for example /subscribe Bukit Timah\n"
    "/unsubscribe <area>: stop alerts for one area\n"
    "/unsubscribe all: stop all alerts and delete your data\n"
    "/list: show the areas you follow\n"
    "/status: current flood risk for your areas, or /status <area>\n"
    "/channel: link to the FloodSense channel, which posts alerts for all areas\n"
    "/help: show this message\n\n"
    f"You can follow up to {MAX_AREAS_PER_CHAT} areas. Alerts follow the same rules as the "
    "FloodSense channel: a message only when the risk rises, and at most one per area per hour "
    "(a rise from Moderate to High is let through).\n\n"
    "Replies come within seconds. Alerts follow the risk feed, which updates every 30 minutes.\n\n"
    f"{HONESTY_LINE}\n\n{PRIVACY_NOTE}"
)
