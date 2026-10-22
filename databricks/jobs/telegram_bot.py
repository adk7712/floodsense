"""
FloodSense - Post Telegram alerts from the newest gold step (Databricks job task).

Runs as the third task of the rainfall poller job, after ``refresh_pipeline``, so it reads gold
right after the pipeline has scored the new readings. Every argument except ``--warehouse-id`` is
passed to ``python -m floodsense.alerts.telegram_bot`` (for example ``live --once --state-file
...``).

Serverless job tasks cannot set environment variables, so the SQL warehouse comes in as
``--warehouse-id`` and is set before ``floodsense`` is imported (its settings are read at import).
The bot token and chat id are read from the secrets scope ``floodsense`` by the bot itself.

Per-area subscriptions are switched on by also passing ``--subscriptions-file
/Volumes/workspace/floodsense/artifacts/telegram_subscriptions.json`` (next to the alert state
file). Each run then reads new bot messages (/subscribe, /unsubscribe, /list, /help), replies, and
sends alerts to subscribers as well as the channel. The file holds chat ids, areas and the last
handled update id, nothing else.
"""

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--warehouse-id", required=True)
    args, rest = parser.parse_known_args(argv)
    os.environ["FLOODSENSE_DATABRICKS_WAREHOUSE_ID"] = args.warehouse_id

    from floodsense.alerts.telegram_bot import main as bot_main

    return bot_main(rest)


if __name__ == "__main__":
    # Databricks runs the file under IPython, which reports any SystemExit (even 0) as a failure,
    # so exit only on an error.
    if code := main():
        sys.exit(code)
