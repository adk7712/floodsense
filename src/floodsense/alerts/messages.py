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
