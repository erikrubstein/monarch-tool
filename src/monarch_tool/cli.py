import argparse
import asyncio
import sys

from .auth import authenticate
from .flows.match_receipts import run_match_flow
from .terminal import enable_utf8_output, supports_color
from .flows.review import get_transactions_needing_review, print_review_transactions


async def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="Simple CLI for retrieving data from Monarch Money.")
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument(
        "--review",
        dest="review",
        action="store_true",
        help="Show transactions that still need review.",
    )
    mode_group.add_argument(
        "--match-receipt",
        dest="match_receipt",
        action="store_true",
        help="Interactive receipt matching and split categorization flow.",
    )
    parser.add_argument("--no-color", dest="no_color", action="store_true", help="Disable ANSI colors in output.")
    parser.add_argument(
        "--details",
        dest="details",
        action="store_true",
        help="Show full details for each transaction below the main row (review mode).",
    )
    args = parser.parse_args()

    if not args.review and not args.match_receipt:
        parser.print_help()
        return 0

    mm = await authenticate()
    review_transactions = await get_transactions_needing_review(mm)
    use_color = supports_color() and not args.no_color

    if args.review:
        print_review_transactions(
            review_transactions,
            use_color=use_color,
            show_details=args.details,
        )
        return 0

    return await run_match_flow(mm, review_transactions=review_transactions, use_color=use_color)


def main() -> int:
    try:
        enable_utf8_output()
        return asyncio.run(_run_cli())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1
