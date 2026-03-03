import argparse
import asyncio
import sys

from .auth import authenticate
from .flows.match_receipts import run_match_flow
from .receipt_sync_accounts import (
    connect_receipt_sync_account,
    list_receipt_sync_accounts,
    remove_receipt_sync_account,
)
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
    mode_group.add_argument(
        "--connect-receipt-account",
        dest="connect_receipt_account",
        action="store_true",
        help="Add or refresh a receipt-sync account (separate from the primary CLI login).",
    )
    mode_group.add_argument(
        "--list-receipt-accounts",
        dest="list_receipt_accounts",
        action="store_true",
        help="List configured receipt-sync accounts.",
    )
    mode_group.add_argument(
        "--remove-receipt-account",
        dest="remove_receipt_account",
        action="store_true",
        help="Remove a receipt-sync account via interactive selection.",
    )
    parser.add_argument("--no-color", dest="no_color", action="store_true", help="Disable ANSI colors in output.")
    parser.add_argument(
        "--details",
        dest="details",
        action="store_true",
        help="Show full details for each transaction below the main row (review mode).",
    )
    args = parser.parse_args()
    use_color = supports_color() and not args.no_color

    if (
        not args.review
        and not args.match_receipt
        and not args.connect_receipt_account
        and not args.list_receipt_accounts
        and not args.remove_receipt_account
    ):
        parser.print_help()
        return 0

    if args.list_receipt_accounts:
        return list_receipt_sync_accounts(use_color=use_color)

    if args.connect_receipt_account:
        return await connect_receipt_sync_account(use_color=use_color)

    if args.remove_receipt_account:
        return remove_receipt_sync_account(use_color=use_color)

    mm = await authenticate()
    review_transactions = await get_transactions_needing_review(mm)

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
