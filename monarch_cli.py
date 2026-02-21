import argparse
import asyncio
from contextlib import contextmanager
import getpass
import os
import re
import shutil
import sys
import textwrap
from decimal import Decimal, ROUND_HALF_UP
from itertools import cycle
from typing import Awaitable
from typing import Any, Dict, List, Optional, Tuple

from monarch import Monarch, RequireMFAException


BACK = "__back__"
QUIT = "__quit__"
CENT = Decimal("0.01")


def _enable_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _supports_emoji() -> bool:
    encoding = sys.stdout.encoding or "utf-8"
    try:
        "⏳🔎✅🔁✂📎·".encode(encoding)
        return True
    except UnicodeEncodeError:
        return False


def _highlight_line(text: str, color: str, use_color: bool) -> str:
    if not use_color:
        return text
    start = f"\033[{color}m"
    end = "\033[0m"
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return f"{start}{plain}{end}"


def _pad_ansi(text: str, width: int, align: str = "left") -> str:
    visible = re.sub(r"\x1b\[[0-9;]*m", "", text)
    pad = max(0, width - len(visible))
    if pad == 0:
        return text
    if align == "right":
        return (" " * pad) + text
    return text + (" " * pad)


async def _with_spinner(awaitable: Awaitable[Any]) -> Any:
    if not sys.stdout.isatty():
        return await awaitable
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    done = asyncio.Event()

    async def _spin() -> None:
        for frame in cycle(frames):
            if done.is_set():
                break
            print(f"\r{frame}", end="", flush=True)
            await asyncio.sleep(0.1)

    task = asyncio.create_task(_spin())
    try:
        result = await awaitable
    finally:
        done.set()
        try:
            await task
        except Exception:
            pass
        print("\r \r", end="", flush=True)
    return result


def _style(text: str, *, color: Optional[str] = None, bold: bool = False, use_color: bool = True) -> str:
    if not use_color:
        return text
    codes: List[str] = []
    if bold:
        codes.append("1")
    if color:
        codes.append(color)
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _format_amount(value: Any) -> str:
    number = _to_decimal(value)
    if number is None:
        return str(value)
    return f"${_to_cents(abs(number)):,.2f}"


def _format_tx_amount(value: Any, use_color: bool, resume_color: Optional[str] = None) -> str:
    number = _to_decimal(value)
    if number is None:
        return _format_amount(value)
    amount = _format_amount(number)
    if use_color and number > 0 and resume_color is None:
        return _style(amount, color="32", use_color=use_color)
    return amount


def _prompt(message: str) -> str:
    try:
        return input(message).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def _tui_enabled() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


@contextmanager
def _hidden_cursor() -> Any:
    if not _tui_enabled():
        yield
        return
    try:
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
        yield
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def _read_key() -> str:
    if os.name == "nt":
        import msvcrt

        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            ext = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(ext, "")
        if key == "\r":
            return "enter"
        if key == " ":
            return "space"
        if key == "\x1b":
            return "esc"
        if key in ("\x08", "\x7f"):
            return "backspace"
        return key.lower()

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        key = sys.stdin.read(1)
        if key == "\x1b":
            ready, _, _ = select.select([sys.stdin], [], [], 0.01)
            if ready:
                next_char = sys.stdin.read(1)
                if next_char == "[":
                    final = sys.stdin.read(1)
                    return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(
                        final, "esc"
                    )
            return "esc"
        if key in ("\r", "\n"):
            return "enter"
        if key == " ":
            return "space"
        if key in ("\x7f", "\b"):
            return "backspace"
        return key.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _choose_single_tui(
    *,
    title: str,
    options: List[str],
    subtitle_lines: Optional[List[str]] = None,
    instructions: Optional[str] = None,
    use_color: bool,
    searchable: bool = False,
    selectable: Optional[List[bool]] = None,
    search_texts: Optional[List[str]] = None,
    marker_mode: str = "cursor",
) -> Any:
    if not options:
        return BACK

    selectable_flags = selectable or [True] * len(options)
    search_source = search_texts or options
    cursor = 0
    query = ""
    message = ""

    with _hidden_cursor():
        while True:
            filtered_indexes = list(range(len(options)))
            if searchable and query:
                term = query.lower()
                filtered_indexes = [
                    idx for idx, option in enumerate(search_source) if term in option.lower()
                ]
                if selectable:
                    heading_indexes: List[int] = []
                    for idx in filtered_indexes:
                        if selectable_flags[idx]:
                            for prev in range(idx - 1, -1, -1):
                                if not selectable_flags[prev]:
                                    heading_indexes.append(prev)
                                    break
                    if heading_indexes:
                        include = set(filtered_indexes + heading_indexes)
                        filtered_indexes = [idx for idx in range(len(options)) if idx in include]

            selectable_indexes = [idx for idx in filtered_indexes if selectable_flags[idx]]
            if cursor >= len(selectable_indexes):
                cursor = max(0, len(selectable_indexes) - 1)

            lines: List[str] = []
            lines.append(_style(title, bold=True, color="36", use_color=use_color))
            if subtitle_lines:
                lines.extend(subtitle_lines)
            if searchable:
                lines.append(f"Search: {query or '(type to filter)'}")
            if message:
                lines.append(_style(message, color="33", use_color=use_color))
            lines.append(instructions or "↑/↓ move, Enter select, b back, q quit")
            lines.append("")

            height = shutil.get_terminal_size(fallback=(120, 30)).lines
            visible = max(5, height - len(lines) - 2)

            if selectable_indexes:
                cursor_idx = selectable_indexes[cursor]
                cursor_pos = filtered_indexes.index(cursor_idx)
                start = max(0, cursor_pos - visible + 1)
                end = min(len(filtered_indexes), start + visible)
                if cursor_pos < start:
                    start = cursor_pos
                    end = min(len(filtered_indexes), start + visible)
                if cursor_pos >= end:
                    start = cursor_pos - visible + 1
                    end = min(len(filtered_indexes), start + visible)

                display_indexes = filtered_indexes[start:end]

                for option_idx in display_indexes:
                    is_selectable = selectable_flags[option_idx]
                    is_cursor = is_selectable and option_idx == cursor_idx
                    prefix = "  "
                    if is_selectable and marker_mode == "all":
                        prefix = "\u25cf" if is_cursor else "\u25cb"
                    elif is_selectable and marker_mode == "cursor":
                        prefix = "\u25cf" if is_cursor else " "
                    line = f"{prefix} {options[option_idx]}"
                    if is_cursor:
                        line = _highlight_line(line, color="33", use_color=use_color)
                    lines.append(line)
            else:
                lines.append("  (no matches)")

            _clear_screen()
            print("\n".join(lines))

            key = _read_key()
            message = ""

            if key in {"up", "k"}:
                cursor = max(0, cursor - 1)
                continue
            if key in {"down", "j"}:
                max_cursor = max(0, len(selectable_indexes) - 1)
                cursor = min(max_cursor, cursor + 1)
                continue
            if key == "enter":
                if selectable_indexes:
                    return selectable_indexes[cursor]
                message = "Nothing matches your filter."
                continue
            if key in {"b", "esc"}:
                return BACK
            if key == "q":
                return QUIT
            if searchable and key == "backspace":
                query = query[:-1]
                cursor = 0
                continue
            if searchable and len(key) == 1 and key.isprintable():
                if key in {"b", "q"} and not query:
                    continue
                query += key
                cursor = 0


async def _authenticate() -> Monarch:
    mm = Monarch()
    session_path = mm._session_file
    if os.path.exists(session_path):
        try:
            mm.load_session(session_path)
            await _with_spinner(mm.get_accounts())
            return mm
        except Exception:
            print("Saved session is no longer valid. Re-authentication required.")
            try:
                mm.delete_session(session_path)
            except Exception:
                pass

    async def _login_with_prompts(email: str, password: str) -> Monarch:
        mm = Monarch()
        try:
            await mm.login(
                email=email,
                password=password,
                use_saved_session=False,
                save_session=True,
            )
            return mm
        except RequireMFAException:
            mfa_code = _prompt("Two-factor code: ")
            try:
                await mm.multi_factor_authenticate(email, password, mfa_code)
            except Exception as exc:
                raise RuntimeError(
                    "Manual MFA code login failed in the installed monarch package. "
                    "Use your MFA app to generate a new code and try again."
                ) from exc
            try:
                mm.save_session()
            except Exception:
                pass
            return mm

    email = _prompt("Monarch email: ")
    password = getpass.getpass("Monarch password: ")
    if not email or not password:
        raise RuntimeError("Email and password are required.")

    return await _login_with_prompts(email, password)


async def _get_transactions_needing_review(mm: Monarch) -> List[Dict[str, Any]]:
    transactions: List[Dict[str, Any]] = []
    offset = 0
    limit = 100
    total_count: Optional[int] = None

    while total_count is None or offset < total_count:
        response = await _with_spinner(mm.get_transactions(limit=limit, offset=offset))
        all_transactions = (response or {}).get("allTransactions", {})
        total_count = all_transactions.get("totalCount", 0)
        page = all_transactions.get("results", [])
        if not page:
            break
        transactions.extend(tx for tx in page if tx.get("needsReview") is True)
        offset += len(page)

    return transactions


def _review_status_marker(review_status: Any, needs_review_marker: str, reviewed_marker: str, unknown_marker: str, empty_marker: str) -> str:
    if not review_status:
        return empty_marker
    value = str(review_status).strip().lower().replace("-", "_")
    if "need" in value or "not_review" in value or "unreview" in value:
        return needs_review_marker
    if "reviewed" in value or value == "ok":
        return reviewed_marker
    return unknown_marker


def _to_bool_marker(value: Any, marker: str, empty_marker: str) -> str:
    return marker if bool(value) else empty_marker


def _format_tags(tx: Dict[str, Any]) -> str:
    tags = tx.get("tags") or []
    names = [str(tag.get("name")) for tag in tags if isinstance(tag, dict) and tag.get("name")]
    return ", ".join(names) if names else "-"


def _format_attachments(tx: Dict[str, Any]) -> str:
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


def _print_detail_lines(tx: Dict[str, Any], columns: int) -> None:
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
        f"tags={_format_tags(tx)}",
        f"attachments={_format_attachments(tx)}",
        f"notes={tx.get('notes') or '-'}",
        f"createdAt={tx.get('createdAt', '-')} | updatedAt={tx.get('updatedAt', '-')}",
    ]

    content_width = max(20, columns - 4)
    for line in detail_lines:
        wrapped = textwrap.wrap(str(line), width=content_width, replace_whitespace=False, drop_whitespace=False)
        if not wrapped:
            print("    ")
            continue
        for part in wrapped:
            print(f"    {part}")


def _merchant_name(tx: Dict[str, Any]) -> str:
    return (tx.get("merchant") or {}).get("name") or tx.get("plaidName") or "Unknown merchant"


def _split_marker(tx: Dict[str, Any], use_color: bool) -> Tuple[str, str]:
    if not bool(tx.get("isSplitTransaction")):
        return "", ""
    raw = " \u2387"
    return raw, _style(raw, color="36", use_color=use_color)


def _format_transaction_row(tx: Dict[str, Any], use_color: bool, *, include_index: Optional[int] = None, highlight_color: Optional[str] = None) -> str:
    date = str(tx.get("date", "unknown-date"))
    amount = _format_tx_amount(tx.get("amount"), use_color=use_color, resume_color=highlight_color)
    amount = _pad_ansi(amount, 10, align="right")
    split_raw, split_marker = _split_marker(tx, use_color=use_color)
    merchant_width = 34 - len(split_raw)
    merchant = _truncate(_merchant_name(tx), merchant_width)
    account = _truncate((tx.get("account") or {}).get("displayName", "Unknown account"), 24)
    base = f"{date}  {amount}  {merchant:<{merchant_width}}{split_marker}  {account}"
    if include_index is None:
        return base
    return f"[{include_index:>2}] {base}"


def _print_review_transactions(transactions: List[Dict[str, Any]], use_color: bool, show_details: bool, use_emoji: bool) -> None:
    if not transactions:
        print(_style("No transactions need review.", color="32", bold=True, use_color=use_color))
        return

    sorted_transactions = sorted(transactions, key=lambda item: item.get("date", ""), reverse=True)
    amounts = [_to_float(tx.get("amount")) for tx in sorted_transactions]
    numeric_amounts = [value for value in amounts if value is not None]

    columns = shutil.get_terminal_size(fallback=(120, 20)).columns
    divider = "-" * min(columns, 120)

    print(_style("Transactions Needing Review", color="36", bold=True, use_color=use_color))
    print(
        f"{len(sorted_transactions)} item(s) | net {_format_amount(sum(numeric_amounts))} | "
        f"in {_format_amount(sum(v for v in numeric_amounts if v > 0))} | "
        f"out {_format_amount(sum(v for v in numeric_amounts if v < 0))}"
    )
    print(divider)

    for tx in sorted_transactions:
        print(_format_transaction_row(tx, use_color=use_color))
        if show_details:
            _print_detail_lines(tx, columns)



def _get_review_transactions_for_match(review_transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [tx for tx in review_transactions if not bool(tx.get("isSplitTransaction"))]


async def _get_active_categories(mm: Monarch) -> List[Dict[str, Any]]:
    response = await _with_spinner(mm.get_transaction_categories())
    categories = response.get("categories", []) if isinstance(response, dict) else []
    return [cat for cat in categories if not cat.get("isDisabled")]


async def _get_retail_syncs(mm: Monarch) -> List[Dict[str, Any]]:
    syncs: List[Dict[str, Any]] = []
    offset = 0
    limit = 50
    total: Optional[int] = None

    while total is None or offset < total:
        response = await _with_spinner(
            mm.get_retail_syncs_with_total(
            filters={},
            offset=offset,
            limit=limit,
            include_total_count=True,
            )
        )
        payload = response.get("retailSyncsWithTotal", {}) if isinstance(response, dict) else {}
        total = payload.get("totalCount")
        results = payload.get("results") or []
        if not results:
            break
        syncs.extend(results)
        offset += len(results)

    return syncs


def _amount_match(a: Any, b: Any) -> bool:
    a_dec = _to_decimal(a)
    b_dec = _to_decimal(b)
    if a_dec is None or b_dec is None:
        return False
    return _to_cents(abs(a_dec)) == _to_cents(abs(b_dec))


def _build_order_candidates(transaction: Dict[str, Any], syncs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    tx_amount = transaction.get("amount")
    tx_id = transaction.get("id")

    for sync in syncs:
        for order in sync.get("orders", []) or []:
            if not _amount_match(order.get("grandTotal"), tx_amount):
                continue
            retail_transactions = order.get("retailTransactions") or []
            already_matched = any(
                ((retail_tx.get("transaction") or {}).get("id") == tx_id)
                for retail_tx in retail_transactions
                if isinstance(retail_tx, dict)
            )
            candidates.append(
                {
                    "sync": sync,
                    "order": order,
                    "line_items": order.get("retailLineItems") or [],
                    "retail_transactions": retail_transactions,
                    "already_matched": already_matched,
                }
            )

    candidates.sort(
        key=lambda item: (
            str((item.get("order") or {}).get("date", "")),
            str((item.get("sync") or {}).get("createdAt", "")),
        ),
        reverse=True,
    )
    return candidates


def _select_transaction_for_match(transactions: List[Dict[str, Any]], use_color: bool) -> Optional[Dict[str, Any]]:
    if not transactions:
        print("No review transactions available.")
        return None

    if _tui_enabled():
        options = [_format_transaction_row(tx, use_color=use_color) for tx in transactions]
        selection = _choose_single_tui(
            title="Pick A Transaction",
            subtitle_lines=["Only transactions needing review with receipt candidates are shown."],
            options=options,
            use_color=use_color,
            marker_mode="cursor",
            instructions="↑/↓ move, Enter select, b/esc back, q quit",
        )
        if selection in {BACK, QUIT}:
            return None
        return transactions[selection]

    columns = shutil.get_terminal_size(fallback=(120, 20)).columns
    divider = "-" * min(columns, 120)
    print(_style("Pick A Transaction", bold=True, color="36", use_color=use_color))
    print("Only transactions needing review with receipt candidates are shown.")
    print(divider)

    for index, tx in enumerate(transactions, start=1):
        print(_format_transaction_row(tx, use_color=use_color, include_index=index))

    while True:
        raw = _prompt("\nSelect transaction #, or q to quit: ").lower()
        if raw in {"q", "quit"}:
            return None
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(transactions):
                return transactions[choice - 1]
        print("Invalid selection.")


def _select_order_candidate(candidates: List[Dict[str, Any]], transaction: Dict[str, Any], use_color: bool) -> Any:
    if not candidates:
        print("No retail sync/order candidates found with matching grand total.")
        return BACK

    if _tui_enabled():
        options: List[str] = []
        for candidate in candidates:
            order = candidate["order"]
            sync = candidate["sync"]
            line_item_count = len(candidate["line_items"])
            tx_amount = _to_decimal(transaction.get("amount")) or Decimal("0")
            amount_value = _to_decimal(order.get("grandTotal")) or Decimal("0")
            signed_amount = amount_value if tx_amount >= 0 else -amount_value
            amount = _pad_ansi(_format_tx_amount(signed_amount, use_color=use_color), 10, align="right")
            options.append(
                f"{order.get('date', '-')}  "
                f"{amount}  "
                f"{_truncate(str(order.get('merchantName', '-')), 24):<24}  "
                f"{line_item_count} items  "
                f"{order.get('displayStatus') or sync.get('status') or '-'}"
            )

        selection = _choose_single_tui(
            title="Pick A Matching Receipt",
            subtitle_lines=[
                f"Transaction: {transaction.get('date', '-')} | "
                f"{_format_amount(transaction.get('amount'))} | {_merchant_name(transaction)}"
            ],
            options=options,
            use_color=use_color,
            marker_mode="cursor",
            instructions="↑/↓ move, Enter select, b/esc back, q quit",
        )
        if selection in {BACK, QUIT}:
            return selection
        return candidates[selection]

    columns = shutil.get_terminal_size(fallback=(120, 20)).columns
    divider = "-" * min(columns, 120)
    print()
    print(_style("Pick A Matching Receipt", bold=True, color="36", use_color=use_color))
    print(f"Transaction: {transaction.get('date', '-')} | {_format_amount(transaction.get('amount'))} | {_merchant_name(transaction)}")
    print(divider)

    for index, candidate in enumerate(candidates, start=1):
        order = candidate["order"]
        sync = candidate["sync"]
        line_item_count = len(candidate["line_items"])
        tx_amount = _to_decimal(transaction.get("amount")) or Decimal("0")
        amount_value = _to_decimal(order.get("grandTotal")) or Decimal("0")
        signed_amount = amount_value if tx_amount >= 0 else -amount_value
        amount = _pad_ansi(_format_tx_amount(signed_amount, use_color=use_color), 10, align="right")
        print(
            f"[{index:>2}] {order.get('date', '-')}  "
            f"{amount}  "
            f"{_truncate(str(order.get('merchantName', '-')), 24):<24}  "
            f"{line_item_count} items  "
            f"{order.get('displayStatus') or sync.get('status') or '-'}"
        )

    while True:
        raw = _prompt("\nSelect receipt #, b to go back, q to quit: ").lower()
        if raw in {"b", "back"}:
            return BACK
        if raw in {"q", "quit"}:
            return QUIT
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(candidates):
                return candidates[choice - 1]
        print("Invalid selection.")


def _category_group_type(category: Dict[str, Any]) -> str:
    group = category.get("group") or {}
    return str(group.get("type", "")).lower() or "other"


def _category_group_name(category: Dict[str, Any]) -> str:
    group = category.get("group") or {}
    return group.get("name") or "Ungrouped"


def _build_category_menu(
    categories: List[Dict[str, Any]], preferred_type: Optional[str], use_color: bool
) -> Tuple[List[str], List[bool], List[str], List[Optional[Dict[str, Any]]]]:
    color_by_type = {"income": "32", "expense": "31", "transfer": "36", "other": "36"}

    options: List[str] = []
    selectable: List[bool] = []
    search_texts: List[str] = []
    category_for_option: List[Optional[Dict[str, Any]]] = []

    group_items: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    group_order: List[Tuple[str, str]] = []
    for cat in categories:
        group_type = _category_group_type(cat)
        group_name = _category_group_name(cat)
        group_key = (group_type, group_name)
        if group_key not in group_items:
            group_items[group_key] = []
            group_order.append(group_key)
        group_items[group_key].append(cat)

    for group_type, group_name in group_order:
        heading = _style(group_name, color=color_by_type.get(group_type, "36"), bold=True, use_color=use_color)
        options.append(heading)
        selectable.append(False)
        search_texts.append("")
        category_for_option.append(None)
        for cat in group_items[(group_type, group_name)]:
            options.append(f"  {cat.get('name', 'Unknown')}")
            selectable.append(True)
            search_texts.append(f"{group_name} {cat.get('name', '')}".lower())
            category_for_option.append(cat)

    return options, selectable, search_texts, category_for_option


def _select_category(categories: List[Dict[str, Any]], preferred_type: Optional[str], use_color: bool) -> Any:
    if _tui_enabled():
        options, selectable, search_texts, category_for_option = _build_category_menu(
            categories, preferred_type, use_color
        )
        subtitle = ["Income, expense, and transfer categories are available."]
        selection = _choose_single_tui(
            title="Select Category",
            subtitle_lines=subtitle,
            options=options,
            selectable=selectable,
            search_texts=search_texts,
            use_color=use_color,
            searchable=True,
            marker_mode="cursor",
            instructions="↑/↓ move, Enter select, type to filter, Backspace delete, b/esc back, q quit",
        )
        if selection in {BACK, QUIT}:
            return selection
        return category_for_option[selection]

    search = ""
    page = 0
    page_size = 20
    options, selectable, search_texts, category_for_option = _build_category_menu(
        categories, preferred_type, use_color=False
    )
    selectable_categories = [
        (idx, cat)
        for idx, cat in enumerate(category_for_option)
        if cat is not None
    ]

    while True:
        view = selectable_categories
        if search:
            term = search.lower()
            view = [
                (idx, cat)
                for idx, cat in selectable_categories
                if term in search_texts[idx]
            ]

        if not view:
            print("No categories match this search.")
            search = ""
            page = 0
            continue

        total_pages = max(1, (len(view) + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        page_items = view[page * page_size : (page + 1) * page_size]

        print()
        print(_style("Select Category", bold=True, color="36", use_color=use_color))
        if preferred_type:
            print(f"Preferred type: {preferred_type}.")
        print(f"Page {page + 1}/{total_pages} | Commands: n/p page, s <text> search, c clear, b back, q quit")
        for idx, (_, category) in enumerate(page_items, start=1):
            group_name = _category_group_name(category)
            print(f"[{idx:>2}] {group_name} / {category.get('name', 'Unknown')}")

        raw = _prompt("Category selection: ")
        lower = raw.lower()
        if lower in {"b", "back"}:
            return BACK
        if lower in {"q", "quit"}:
            return QUIT
        if lower == "n":
            page += 1
            continue
        if lower == "p":
            page -= 1
            continue
        if lower == "c":
            search = ""
            page = 0
            continue
        if lower.startswith("s "):
            search = raw[2:].strip()
            page = 0
            continue
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(page_items):
                return page_items[choice - 1][1]
        print("Invalid selection.")

def _line_item_total(item: Dict[str, Any]) -> Decimal:
    return abs(_to_cents(_to_decimal(item.get("total")) or Decimal("0")))


def _signed_line_item_total(item: Dict[str, Any], transaction_amount: Any) -> Decimal:
    base = _line_item_total(item)
    sign = -1 if (_to_decimal(transaction_amount) or 0) < 0 else 1
    return base * sign


def _line_item_label(item: Dict[str, Any]) -> str:
    qty = item.get("quantity")
    qty_label = f"x{qty}" if qty not in (None, "") else ""
    title = item.get("title", "Untitled item")
    return f"{qty_label} {title}".strip()


def _select_line_items_and_categories_tui(
    order: Dict[str, Any],
    categories: List[Dict[str, Any]],
    transaction_amount: Any,
    use_color: bool,
    initial_assignments: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Any:
    line_items = order.get("retailLineItems") or []
    if not line_items:
        print("Selected receipt has no line items.")
        return BACK

    index_to_item: Dict[int, Dict[str, Any]] = {i: item for i, item in enumerate(line_items)}
    assignments: Dict[str, Dict[str, Any]] = dict(initial_assignments or {})
    selected: set[int] = set()
    history: List[Dict[str, Optional[Dict[str, Any]]]] = []
    cursor = 0
    status = ""
    preferred_type = "expense" if (_to_decimal(transaction_amount) or 0) < 0 else "income"

    with _hidden_cursor():
        while True:
            lines: List[str] = []
            lines.append(
                _style(
                    "Assign Categories To Line Items",
                    bold=True,
                    color="36",
                    use_color=use_color,
                )
            )
            lines.append(
                "↑/↓ move | space select | enter assign category | "
                "u undo | d done | b back | q quit"
            )
            if status:
                lines.append(_style(status, color="33", use_color=use_color))

            assigned_count = sum(
                1 for item in line_items if str(item.get("id")) in assignments
            )
            lines.append(f"Assigned: {assigned_count}/{len(line_items)}")
            lines.append("")

            height = shutil.get_terminal_size(fallback=(120, 30)).lines
            visible = max(6, height - len(lines) - 2)

            start = max(0, cursor - visible + 1)
            end = min(len(line_items), start + visible)
            if cursor < start:
                start = cursor
            if cursor >= end:
                start = cursor - visible + 1
                end = min(len(line_items), start + visible)

            for idx in range(start, end):
                item = index_to_item[idx]
                item_id = str(item.get("id"))
                assigned_category = assignments.get(item_id)
                assigned_label = (
                    assigned_category.get("name", "-") if assigned_category else "unassigned"
                )
                amount = _pad_ansi(
                    _format_tx_amount(_signed_line_item_total(item, transaction_amount), use_color=use_color),
                    10,
                    align="right",
                )
                marker = "\u25cf" if idx in selected else "\u25cb"
                line = (
                    f"{marker} {idx + 1:>2}. {amount}  "
                    f"{_truncate(_line_item_label(item), 42):<42} -> {assigned_label}"
                )
                if idx == cursor:
                    line = _highlight_line(line, color="33", use_color=use_color)
                elif idx in selected:
                    line = _style(line, color="96", use_color=use_color)
                elif assigned_category:
                    line = _style(line, color="90", use_color=use_color)
                lines.append(line)

            _clear_screen()
            print("\n".join(lines))

            key = _read_key()
            status = ""

            if key in {"up", "k"}:
                cursor = max(0, cursor - 1)
                continue
            if key in {"down", "j"}:
                cursor = min(len(line_items) - 1, cursor + 1)
                continue
            if key == "space":
                if cursor in selected:
                    selected.remove(cursor)
                else:
                    selected.add(cursor)
                continue
            if key in {"b", "esc"}:
                return BACK
            if key == "q":
                return QUIT
            if key == "u":
                if not history:
                    status = "Nothing to undo."
                    continue
                previous = history.pop()
                for item_id, old_cat in previous.items():
                    if old_cat is None:
                        assignments.pop(item_id, None)
                    else:
                        assignments[item_id] = old_cat
                continue
            if key == "d":
                if assigned_count == len(line_items):
                    return assignments
                status = "Assign all items before continuing."
                continue
            if key == "enter":
                if not selected:
                    if assigned_count == len(line_items):
                        return assignments
                    status = "Assign all items before continuing."
                    continue
                target_indexes = sorted(selected) if selected else [cursor]
                category = _select_category(
                    categories,
                    preferred_type=preferred_type,
                    use_color=use_color,
                )
                if category == BACK:
                    continue
                if category == QUIT:
                    return QUIT

                previous_state: Dict[str, Optional[Dict[str, Any]]] = {}
                for idx in target_indexes:
                    item_id = str(index_to_item[idx].get("id"))
                    previous_state[item_id] = assignments.get(item_id)
                    assignments[item_id] = category
                history.append(previous_state)
                selected.clear()
                continue


def _select_line_items_and_categories(
    order: Dict[str, Any],
    categories: List[Dict[str, Any]],
    transaction_amount: Any,
    use_color: bool,
    initial_assignments: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Any:
    if _tui_enabled():
        return _select_line_items_and_categories_tui(
            order=order,
            categories=categories,
            transaction_amount=transaction_amount,
            use_color=use_color,
            initial_assignments=initial_assignments,
        )

    line_items = order.get("retailLineItems") or []
    if not line_items:
        print("Selected receipt has no line items.")
        return BACK

    index_to_item: Dict[int, Dict[str, Any]] = {i + 1: item for i, item in enumerate(line_items)}
    assignments: Dict[str, Dict[str, Any]] = dict(initial_assignments or {})
    history: List[Dict[str, Optional[Dict[str, Any]]]] = []
    preferred_type = "expense" if (_to_decimal(transaction_amount) or 0) < 0 else "income"

    while True:
        print()
        print(_style("Assign Categories To Line Items", bold=True, color="36", use_color=use_color))
        print("Commands: numbers (e.g. 1,3), a=all unassigned, u=undo, d=done, b=back, q=quit")

        all_assigned = True
        for index, item in index_to_item.items():
            item_id = str(item.get("id"))
            assigned_category = assignments.get(item_id)
            assigned_label = assigned_category.get("name", "-") if assigned_category else "unassigned"
            amount = _pad_ansi(
                _format_tx_amount(_signed_line_item_total(item, transaction_amount), use_color=use_color),
                10,
                align="right",
            )
            marker = "[x]" if assigned_category else "[ ]"
            line = f"{marker} [{index:>2}] {amount}  {_truncate(_line_item_label(item), 44):<44} -> {assigned_label}"
            if assigned_category:
                print(_style(line, color="37", use_color=use_color))
            else:
                all_assigned = False
                print(line)

        if all_assigned:
            print("All items are assigned. Enter d to continue to split preview, or select items to reassign.")

        raw = _prompt("\nSelect line item(s): ").lower()
        if raw in {"d", "done"}:
            if all_assigned:
                return assignments
            print("You can only use d once all items are assigned.")
            continue
        if raw in {"b", "back"}:
            return BACK
        if raw in {"q", "quit"}:
            return QUIT
        if raw in {"u", "undo"}:
            if not history:
                print("Nothing to undo.")
                continue
            previous = history.pop()
            for item_id, old_cat in previous.items():
                if old_cat is None:
                    assignments.pop(item_id, None)
                else:
                    assignments[item_id] = old_cat
            continue

        if raw in {"a", "all"}:
            selected_indexes = [idx for idx, item in index_to_item.items() if str(item.get("id")) not in assignments]
            if not selected_indexes:
                print("All items are already assigned.")
                continue
        else:
            parts = [part.strip() for part in raw.split(",") if part.strip()]
            if not parts:
                print("Invalid selection.")
                continue
            selected_indexes: List[int] = []
            valid = True
            for part in parts:
                if not part.isdigit():
                    valid = False
                    break
                idx = int(part)
                if idx not in index_to_item:
                    valid = False
                    break
                selected_indexes.append(idx)
            if not valid:
                print("Invalid line item selection.")
                continue
            selected_indexes = sorted(set(selected_indexes))

        category = _select_category(categories, preferred_type=preferred_type, use_color=use_color)
        if category == BACK:
            continue
        if category == QUIT:
            return QUIT

        previous_state: Dict[str, Optional[Dict[str, Any]]] = {}
        for idx in selected_indexes:
            item_id = str(index_to_item[idx].get("id"))
            previous_state[item_id] = assignments.get(item_id)
            assignments[item_id] = category
        history.append(previous_state)


def _distribute_delta(base_by_category_id: Dict[str, Decimal], delta: Decimal) -> Dict[str, Decimal]:
    if not base_by_category_id:
        return {}
    keys = list(base_by_category_id.keys())
    if len(keys) == 1:
        return {keys[0]: delta}

    total_base = sum(base_by_category_id.values(), Decimal("0"))
    if total_base == 0:
        share = _to_cents(delta / Decimal(len(keys)))
        distributed = {key: share for key in keys}
        distributed[keys[-1]] += delta - sum(distributed.values(), Decimal("0"))
        return distributed

    distributed: Dict[str, Decimal] = {}
    running = Decimal("0")
    for key in keys[:-1]:
        share = _to_cents(delta * (base_by_category_id[key] / total_base))
        distributed[key] = share
        running += share
    distributed[keys[-1]] = delta - running
    return distributed


def _build_split_plan(transaction: Dict[str, Any], order: Dict[str, Any], assignments: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Decimal, Decimal, Decimal]:
    tx_amount = _to_cents(_to_decimal(transaction.get("amount")) or Decimal("0"))
    sign = Decimal("-1") if tx_amount < 0 else Decimal("1")
    tx_abs = abs(tx_amount)

    base_by_category: Dict[str, Decimal] = {}
    count_by_category: Dict[str, int] = {}
    category_by_id: Dict[str, Dict[str, Any]] = {}

    line_items = order.get("retailLineItems") or []
    line_total = Decimal("0")
    for item in line_items:
        item_id = str(item.get("id"))
        if item_id not in assignments:
            raise RuntimeError("All line items must be assigned before building splits.")

        category = assignments[item_id]
        category_id = str(category.get("id"))
        item_total = _line_item_total(item)
        line_total += item_total
        base_by_category[category_id] = base_by_category.get(category_id, Decimal("0")) + item_total
        count_by_category[category_id] = count_by_category.get(category_id, 0) + 1
        category_by_id[category_id] = category

    line_total = _to_cents(line_total)
    delta = _to_cents(tx_abs - line_total)
    distributed = _distribute_delta(base_by_category, delta)

    preview_rows: List[Dict[str, Any]] = []
    split_data: List[Dict[str, Any]] = []
    merchant_name = _merchant_name(transaction)

    category_ids = list(base_by_category.keys())
    running_signed = Decimal("0")
    for idx, category_id in enumerate(category_ids):
        category = category_by_id[category_id]
        base = _to_cents(base_by_category[category_id])
        extra = _to_cents(distributed.get(category_id, Decimal("0")))
        split_abs = _to_cents(base + extra)
        split_signed = _to_cents(split_abs * sign)

        if idx == len(category_ids) - 1:
            split_signed = _to_cents(tx_amount - running_signed)
            split_abs = abs(split_signed)
        running_signed += split_signed

        preview_rows.append(
            {
                "category_name": category.get("name", category_id),
                "line_item_count": count_by_category.get(category_id, 0),
                "base": base,
                "delta_share": extra,
                "split_amount": split_signed,
            }
        )
        split_data.append({"merchantName": merchant_name, "amount": float(split_signed), "categoryId": category_id})

    return split_data, preview_rows, tx_amount, line_total, delta


def _print_split_preview(preview_rows: List[Dict[str, Any]], tx_amount: Decimal, line_total: Decimal, delta: Decimal, order: Dict[str, Any], use_color: bool) -> None:
    print()
    print(_style("Split Preview", bold=True, color="36", use_color=use_color))
    print(
        f"Transaction amount: {_format_amount(tx_amount)} | "
        f"Line-item total: {_format_amount(line_total)} | "
        f"Distributed remainder (tax/adjustments): {_format_amount(delta)}"
    )
    order_tax = _to_decimal(order.get("tax"))
    if order_tax is not None:
        print(f"Receipt tax: {_format_amount(order_tax)}")

    rows = [
        {
            "category": str(row.get("category_name", "-")),
            "items": int(row.get("line_item_count") or 0),
            "base": row.get("base"),
            "delta": row.get("delta_share"),
            "split": row.get("split_amount"),
        }
        for row in preview_rows
    ]
    if not rows:
        print("No split rows to display.")
        return
    cat_w = max(12, min(32, max(len(r["category"]) for r in rows)))
    print()
    header = f"{'Category':<{cat_w}}  {'Items':>5}  {'Base':>12}  {'Remainder':>12}  {'Split':>12}"
    print(_style(header, bold=True, color="90", use_color=use_color))
    for row in rows:
        print(
            f"{_truncate(row['category'], cat_w):<{cat_w}}  "
            f"{row['items']:>5}  "
            f"{_format_amount(row['base']):>12}  "
            f"{_format_amount(row['delta']):>12}  "
            f"{_format_amount(row['split']):>12}"
        )
    print()


async def _maybe_match_retail_transaction(mm: Monarch, transaction: Dict[str, Any], candidate: Dict[str, Any]) -> None:
    retail_transactions = candidate.get("retail_transactions") or []
    matching = [rt for rt in retail_transactions if _amount_match(rt.get("total"), transaction.get("amount"))]
    if not matching:
        return

    selected = matching[0]
    if ((selected.get("transaction") or {}).get("id")) == transaction.get("id"):
        return

    response = await _with_spinner(
        mm.match_retail_transaction(
            retail_transaction_id=selected.get("id"),
            transaction_id=transaction.get("id"),
        )
    )
    errors = ((response or {}).get("matchRetailTransaction") or {}).get("errors") or []
    if errors:
        print(f"Warning: could not match retail transaction automatically: {errors[0].get('message', 'unknown error')}")


async def _run_match_flow(mm: Monarch, review_transactions: List[Dict[str, Any]], use_color: bool) -> int:
    transactions = _get_review_transactions_for_match(review_transactions)
    if not transactions:
        print("No eligible transactions. A transaction must need review and not already be split.")
        return 0

    categories = await _get_active_categories(mm)
    if not categories:
        raise RuntimeError("No active categories found in Monarch.")

    print("Loading retail syncs...")
    syncs = await _get_retail_syncs(mm)
    candidates_by_tx: Dict[str, List[Dict[str, Any]]] = {}
    for tx in transactions:
        tx_id = str(tx.get("id", ""))
        candidates = _build_order_candidates(tx, syncs)
        if candidates:
            candidates_by_tx[tx_id] = candidates

    transactions = [tx for tx in transactions if str(tx.get("id", "")) in candidates_by_tx]
    if not transactions:
        print("No eligible transactions with receipt candidates.")
        return 0

    while True:
        selected_tx = _select_transaction_for_match(transactions, use_color=use_color)
        if selected_tx is None:
            return 0
        candidates = candidates_by_tx.get(str(selected_tx.get("id", "")), [])

        while True:
            candidate = _select_order_candidate(candidates, transaction=selected_tx, use_color=use_color)
            if candidate == BACK:
                break
            if candidate == QUIT:
                return 0

            order = candidate["order"]
            assignments: Dict[str, Dict[str, Any]] = {}

            while True:
                assignment_result = _select_line_items_and_categories(
                    order=order,
                    categories=categories,
                    transaction_amount=selected_tx.get("amount"),
                    use_color=use_color,
                    initial_assignments=assignments,
                )
                if assignment_result == BACK:
                    break
                if assignment_result == QUIT:
                    return 0
                assignments = assignment_result

                split_data, preview_rows, tx_amount, line_total, delta = _build_split_plan(selected_tx, order, assignments)
                _print_split_preview(preview_rows, tx_amount, line_total, delta, order, use_color=use_color)
                prompt = _style("Create these splits?", color="36", use_color=use_color) + " [Y]/n, b back, q quit: "
                confirm = _prompt(prompt).strip().lower()
                if confirm == "":
                    confirm = "y"
                if confirm in {"b", "back"}:
                    continue
                if confirm in {"q", "quit"}:
                    return 0
                if confirm not in {"y", "yes"}:
                    print("Invalid choice.")
                    continue

                await _maybe_match_retail_transaction(mm, selected_tx, candidate)
                response = await _with_spinner(
                    mm.update_transaction_splits(
                        transaction_id=selected_tx.get("id"),
                        split_data=split_data,
                    )
                )
                errors = ((response or {}).get("updateTransactionSplit") or {}).get("errors") or []
                if errors:
                    raise RuntimeError(f"Failed to create splits: {errors[0].get('message', 'unknown error')}")

                print(_style("Split transaction updated successfully.", color="32", bold=True, use_color=use_color))
                return 0


async def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="Simple CLI for retrieving data from Monarch Money.")
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument("--review", dest="review", action="store_true", help="Show transactions that still need review.")
    mode_group.add_argument("--match-receipt", dest="match_receipt", action="store_true", help="Interactive receipt matching and split categorization flow.")
    parser.add_argument("--no-color", dest="no_color", action="store_true", help="Disable ANSI colors in output.")
    parser.add_argument("--details", dest="details", action="store_true", help="Show full details for each transaction below the main row (review mode).")
    args = parser.parse_args()

    if not args.review and not args.match_receipt:
        parser.print_help()
        return 0

    mm = await _authenticate()
    review_transactions = await _get_transactions_needing_review(mm)
    use_color = _supports_color() and not args.no_color

    if args.review:
        _print_review_transactions(
            review_transactions,
            use_color=use_color,
            show_details=args.details,
            use_emoji=_supports_emoji(),
        )
        return 0

    return await _run_match_flow(mm, review_transactions=review_transactions, use_color=use_color)


def main() -> int:
    try:
        _enable_utf8_output()
        return asyncio.run(_run_cli())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
