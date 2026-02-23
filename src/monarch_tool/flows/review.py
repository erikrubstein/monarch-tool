import shutil
import textwrap
from typing import Any, Dict, List, Optional, Tuple

from ..formatting import (
    format_amount,
    format_tx_amount,
    pad_ansi,
    style,
    to_float,
    truncate,
)
from ..terminal import with_spinner


async def get_transactions_needing_review(mm: Any) -> List[Dict[str, Any]]:
    transactions: List[Dict[str, Any]] = []
    offset = 0
    limit = 100
    total_count: Optional[int] = None

    while total_count is None or offset < total_count:
        response = await with_spinner(mm.get_transactions(limit=limit, offset=offset))
        all_transactions = (response or {}).get("allTransactions", {})
        total_count = all_transactions.get("totalCount", 0)
        page = all_transactions.get("results", [])
        if not page:
            break
        transactions.extend(tx for tx in page if tx.get("needsReview") is True)
        offset += len(page)

    return transactions


def format_tags(tx: Dict[str, Any]) -> str:
    tags = tx.get("tags") or []
    names = [str(tag.get("name")) for tag in tags if isinstance(tag, dict) and tag.get("name")]
    return ", ".join(names) if names else "-"


def format_attachments(tx: Dict[str, Any]) -> str:
    attachments = tx.get("attachments") or []
    if not attachments:
        return "-"
    filenames = [
        str(attachment.get("filename"))
        for attachment in attachments
        if isinstance(attachment, dict) and attachment.get("filename")
    ]
    if filenames:
        return ", ".join(filenames)
    return f"{len(attachments)} file(s)"


def print_detail_lines(tx: Dict[str, Any], columns: int) -> None:
    detail_lines = [
        (
            "id={id} | reviewStatus={status} | needsReview={needs_review} | pending={pending} | "
            "recurring={recurring} | split={split_tx} | hideFromReports={hidden}"
        ).format(
            id=tx.get("id", "-"),
            status=tx.get("reviewStatus", "-"),
            needs_review=tx.get("needsReview", False),
            pending=tx.get("pending", False),
            recurring=tx.get("isRecurring", False),
            split_tx=tx.get("isSplitTransaction", False),
            hidden=tx.get("hideFromReports", False),
        ),
        f"plaidName={tx.get('plaidName', '-')}",
        f"tags={format_tags(tx)}",
        f"attachments={format_attachments(tx)}",
        f"notes={tx.get('notes') or '-'}",
        f"createdAt={tx.get('createdAt', '-')} | updatedAt={tx.get('updatedAt', '-')}",
    ]

    content_width = max(20, columns - 4)
    for line in detail_lines:
        wrapped = textwrap.wrap(
            str(line),
            width=content_width,
            replace_whitespace=False,
            drop_whitespace=False,
        )
        if not wrapped:
            print("    ")
            continue
        for part in wrapped:
            print(f"    {part}")


def merchant_name(tx: Dict[str, Any]) -> str:
    return (tx.get("merchant") or {}).get("name") or tx.get("plaidName") or "Unknown merchant"


def split_marker(tx: Dict[str, Any], use_color: bool) -> Tuple[str, str]:
    if not bool(tx.get("isSplitTransaction")):
        return "", ""
    raw = " \u2387"
    return raw, style(raw, color="36", use_color=use_color)


def format_transaction_row(
    tx: Dict[str, Any],
    use_color: bool,
    *,
    include_index: Optional[int] = None,
    highlight_color: Optional[str] = None,
) -> str:
    date = str(tx.get("date", "unknown-date"))
    amount = format_tx_amount(tx.get("amount"), use_color=use_color, resume_color=highlight_color)
    amount = pad_ansi(amount, 10, align="right")
    split_raw, split_marker_value = split_marker(tx, use_color=use_color)
    merchant_width = 34 - len(split_raw)
    merchant = truncate(merchant_name(tx), merchant_width)
    account = truncate((tx.get("account") or {}).get("displayName", "Unknown account"), 24)
    base = f"{date}  {amount}  {merchant:<{merchant_width}}{split_marker_value}  {account}"
    if include_index is None:
        return base
    return f"[{include_index:>2}] {base}"


def print_review_transactions(
    transactions: List[Dict[str, Any]],
    use_color: bool,
    show_details: bool,
) -> None:
    if not transactions:
        print(style("No transactions need review.", color="32", bold=True, use_color=use_color))
        return

    sorted_transactions = sorted(transactions, key=lambda item: item.get("date", ""), reverse=True)
    amounts = [to_float(tx.get("amount")) for tx in sorted_transactions]
    numeric_amounts = [value for value in amounts if value is not None]

    columns = shutil.get_terminal_size(fallback=(120, 20)).columns
    divider = "-" * min(columns, 120)

    print(style("Transactions Needing Review", color="36", bold=True, use_color=use_color))
    print(
        f"{len(sorted_transactions)} item(s) | net {format_amount(sum(numeric_amounts))} | "
        f"in {format_amount(sum(v for v in numeric_amounts if v > 0))} | "
        f"out {format_amount(sum(v for v in numeric_amounts if v < 0))}"
    )
    print(divider)

    for tx in sorted_transactions:
        print(format_transaction_row(tx, use_color=use_color))
        if show_details:
            print_detail_lines(tx, columns)


def get_review_transactions_for_match(review_transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [tx for tx in review_transactions if not bool(tx.get("isSplitTransaction"))]
