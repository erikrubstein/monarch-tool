import shutil
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..constants import BACK, QUIT
from ..formatting import (
    format_amount,
    format_tx_amount,
    highlight_line,
    pad_ansi,
    style,
    to_cents,
    to_decimal,
    truncate,
)
from ..menus import choose_single_tui
from ..terminal import clear_screen, hidden_cursor, prompt, read_key, tui_enabled, with_spinner
from .review import format_transaction_row, get_review_transactions_for_match, merchant_name


async def get_active_categories(mm: Any) -> List[Dict[str, Any]]:
    response = await with_spinner(mm.get_transaction_categories())
    categories = response.get("categories", []) if isinstance(response, dict) else []
    return [cat for cat in categories if not cat.get("isDisabled")]


async def get_retail_syncs(mm: Any) -> List[Dict[str, Any]]:
    syncs: List[Dict[str, Any]] = []
    offset = 0
    limit = 50
    total: Optional[int] = None

    while total is None or offset < total:
        response = await with_spinner(
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


def amount_match(a: Any, b: Any) -> bool:
    a_dec = to_decimal(a)
    b_dec = to_decimal(b)
    if a_dec is None or b_dec is None:
        return False
    return to_cents(abs(a_dec)) == to_cents(abs(b_dec))


def build_order_candidates(transaction: Dict[str, Any], syncs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    tx_amount = transaction.get("amount")
    tx_id = transaction.get("id")

    for sync in syncs:
        for order in sync.get("orders", []) or []:
            if not amount_match(order.get("grandTotal"), tx_amount):
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


def select_transaction_for_match(
    transactions: List[Dict[str, Any]], use_color: bool
) -> Optional[Dict[str, Any]]:
    if not transactions:
        print("No review transactions available.")
        return None

    if tui_enabled():
        options = [format_transaction_row(tx, use_color=use_color) for tx in transactions]
        selection = choose_single_tui(
            title="Pick A Transaction",
            subtitle_lines=["Only transactions needing review with receipt candidates are shown."],
            options=options,
            use_color=use_color,
            marker_mode="cursor",
            instructions="\u2191/\u2193 move, Enter select, b/esc back, q quit",
        )
        if selection in {BACK, QUIT}:
            return None
        return transactions[selection]

    columns = shutil.get_terminal_size(fallback=(120, 20)).columns
    divider = "-" * min(columns, 120)
    print(style("Pick A Transaction", bold=True, color="36", use_color=use_color))
    print("Only transactions needing review with receipt candidates are shown.")
    print(divider)

    for index, tx in enumerate(transactions, start=1):
        print(format_transaction_row(tx, use_color=use_color, include_index=index))

    while True:
        raw = prompt("\nSelect transaction #, or q to quit: ").lower()
        if raw in {"q", "quit"}:
            return None
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(transactions):
                return transactions[choice - 1]
        print("Invalid selection.")


def select_order_candidate(
    candidates: List[Dict[str, Any]],
    transaction: Dict[str, Any],
    use_color: bool,
) -> Any:
    if not candidates:
        print("No retail sync/order candidates found with matching grand total.")
        return BACK

    if tui_enabled():
        options: List[str] = []
        for candidate in candidates:
            order = candidate["order"]
            sync = candidate["sync"]
            line_item_count = len(candidate["line_items"])
            tx_amount = to_decimal(transaction.get("amount")) or Decimal("0")
            amount_value = to_decimal(order.get("grandTotal")) or Decimal("0")
            signed_amount = amount_value if tx_amount >= 0 else -amount_value
            amount = pad_ansi(format_tx_amount(signed_amount, use_color=use_color), 10, align="right")
            options.append(
                f"{order.get('date', '-')}  "
                f"{amount}  "
                f"{truncate(str(order.get('merchantName', '-')), 24):<24}  "
                f"{line_item_count} items  "
                f"{order.get('displayStatus') or sync.get('status') or '-'}"
            )

        selection = choose_single_tui(
            title="Pick A Matching Receipt",
            subtitle_lines=[
                f"Transaction: {transaction.get('date', '-')} | "
                f"{format_amount(transaction.get('amount'))} | {merchant_name(transaction)}"
            ],
            options=options,
            use_color=use_color,
            marker_mode="cursor",
            instructions="\u2191/\u2193 move, Enter select, b/esc back, q quit",
        )
        if selection in {BACK, QUIT}:
            return selection
        return candidates[selection]

    columns = shutil.get_terminal_size(fallback=(120, 20)).columns
    divider = "-" * min(columns, 120)
    print()
    print(style("Pick A Matching Receipt", bold=True, color="36", use_color=use_color))
    print(
        f"Transaction: {transaction.get('date', '-')} | "
        f"{format_amount(transaction.get('amount'))} | {merchant_name(transaction)}"
    )
    print(divider)

    for index, candidate in enumerate(candidates, start=1):
        order = candidate["order"]
        sync = candidate["sync"]
        line_item_count = len(candidate["line_items"])
        tx_amount = to_decimal(transaction.get("amount")) or Decimal("0")
        amount_value = to_decimal(order.get("grandTotal")) or Decimal("0")
        signed_amount = amount_value if tx_amount >= 0 else -amount_value
        amount = pad_ansi(format_tx_amount(signed_amount, use_color=use_color), 10, align="right")
        print(
            f"[{index:>2}] {order.get('date', '-')}  "
            f"{amount}  "
            f"{truncate(str(order.get('merchantName', '-')), 24):<24}  "
            f"{line_item_count} items  "
            f"{order.get('displayStatus') or sync.get('status') or '-'}"
        )

    while True:
        raw = prompt("\nSelect receipt #, b to go back, q to quit: ").lower()
        if raw in {"b", "back"}:
            return BACK
        if raw in {"q", "quit"}:
            return QUIT
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(candidates):
                return candidates[choice - 1]
        print("Invalid selection.")


def category_group_type(category: Dict[str, Any]) -> str:
    group = category.get("group") or {}
    return str(group.get("type", "")).lower() or "other"


def category_group_name(category: Dict[str, Any]) -> str:
    group = category.get("group") or {}
    return group.get("name") or "Ungrouped"


def build_category_menu(
    categories: List[Dict[str, Any]],
    preferred_type: Optional[str],
    use_color: bool,
) -> Tuple[List[str], List[bool], List[str], List[Optional[Dict[str, Any]]]]:
    color_by_type = {"income": "32", "expense": "31", "transfer": "36", "other": "36"}

    options: List[str] = []
    selectable: List[bool] = []
    search_texts: List[str] = []
    category_for_option: List[Optional[Dict[str, Any]]] = []

    group_items: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    group_order: List[Tuple[str, str]] = []
    for cat in categories:
        group_type = category_group_type(cat)
        group_name = category_group_name(cat)
        group_key = (group_type, group_name)
        if group_key not in group_items:
            group_items[group_key] = []
            group_order.append(group_key)
        group_items[group_key].append(cat)

    for group_type, group_name in group_order:
        heading = style(
            group_name,
            color=color_by_type.get(group_type, "36"),
            bold=True,
            use_color=use_color,
        )
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


def select_category(
    categories: List[Dict[str, Any]],
    preferred_type: Optional[str],
    use_color: bool,
) -> Any:
    if tui_enabled():
        options, selectable, search_texts, category_for_option = build_category_menu(
            categories, preferred_type, use_color
        )
        subtitle = ["Income, expense, and transfer categories are available."]
        selection = choose_single_tui(
            title="Select Category",
            subtitle_lines=subtitle,
            options=options,
            selectable=selectable,
            search_texts=search_texts,
            use_color=use_color,
            searchable=True,
            marker_mode="cursor",
            instructions="\u2191/\u2193 move, Enter select, type to filter, Backspace delete, b/esc back, q quit",
        )
        if selection in {BACK, QUIT}:
            return selection
        return category_for_option[selection]

    search = ""
    page = 0
    page_size = 20
    options, selectable, search_texts, category_for_option = build_category_menu(
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
        print(style("Select Category", bold=True, color="36", use_color=use_color))
        if preferred_type:
            print(f"Preferred type: {preferred_type}.")
        print(f"Page {page + 1}/{total_pages} | Commands: n/p page, s <text> search, c clear, b back, q quit")
        for idx, (_, category) in enumerate(page_items, start=1):
            group_name = category_group_name(category)
            print(f"[{idx:>2}] {group_name} / {category.get('name', 'Unknown')}")

        raw = prompt("Category selection: ")
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


def line_item_total(item: Dict[str, Any]) -> Decimal:
    return abs(to_cents(to_decimal(item.get("total")) or Decimal("0")))


def signed_line_item_total(item: Dict[str, Any], transaction_amount: Any) -> Decimal:
    base = line_item_total(item)
    sign = -1 if (to_decimal(transaction_amount) or 0) < 0 else 1
    return base * sign


def line_item_label(item: Dict[str, Any]) -> str:
    qty = item.get("quantity")
    qty_label = f"x{qty}" if qty not in (None, "") else ""
    title = item.get("title", "Untitled item")
    return f"{qty_label} {title}".strip()


def select_line_items_and_categories_tui(
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
    preferred_type = "expense" if (to_decimal(transaction_amount) or 0) < 0 else "income"

    with hidden_cursor():
        while True:
            lines: List[str] = []
            lines.append(
                style(
                    "Assign Categories To Line Items",
                    bold=True,
                    color="36",
                    use_color=use_color,
                )
            )
            lines.append(
                "\u2191/\u2193 move | space select | enter assign category | "
                "u undo | d done | b back | q quit"
            )
            if status:
                lines.append(style(status, color="33", use_color=use_color))

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
                amount = pad_ansi(
                    format_tx_amount(signed_line_item_total(item, transaction_amount), use_color=use_color),
                    10,
                    align="right",
                )
                marker = "\u25cf" if idx in selected else "\u25cb"
                line = (
                    f"{marker} {idx + 1:>2}. {amount}  "
                    f"{truncate(line_item_label(item), 42):<42} -> {assigned_label}"
                )
                if idx == cursor:
                    line = highlight_line(line, color="33", use_color=use_color)
                elif idx in selected:
                    line = style(line, color="96", use_color=use_color)
                elif assigned_category:
                    line = style(line, color="90", use_color=use_color)
                lines.append(line)

            clear_screen()
            print("\n".join(lines))

            key = read_key()
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
                category = select_category(
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


def select_line_items_and_categories(
    order: Dict[str, Any],
    categories: List[Dict[str, Any]],
    transaction_amount: Any,
    use_color: bool,
    initial_assignments: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Any:
    if tui_enabled():
        return select_line_items_and_categories_tui(
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
    preferred_type = "expense" if (to_decimal(transaction_amount) or 0) < 0 else "income"

    while True:
        print()
        print(style("Assign Categories To Line Items", bold=True, color="36", use_color=use_color))
        print("Commands: numbers (e.g. 1,3), a=all unassigned, u=undo, d=done, b=back, q=quit")

        all_assigned = True
        for index, item in index_to_item.items():
            item_id = str(item.get("id"))
            assigned_category = assignments.get(item_id)
            assigned_label = assigned_category.get("name", "-") if assigned_category else "unassigned"
            amount = pad_ansi(
                format_tx_amount(signed_line_item_total(item, transaction_amount), use_color=use_color),
                10,
                align="right",
            )
            marker = "[x]" if assigned_category else "[ ]"
            line = f"{marker} [{index:>2}] {amount}  {truncate(line_item_label(item), 44):<44} -> {assigned_label}"
            if assigned_category:
                print(style(line, color="37", use_color=use_color))
            else:
                all_assigned = False
                print(line)

        if all_assigned:
            print("All items are assigned. Enter d to continue to split preview, or select items to reassign.")

        raw = prompt("\nSelect line item(s): ").lower()
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

        category = select_category(categories, preferred_type=preferred_type, use_color=use_color)
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


def distribute_delta(base_by_category_id: Dict[str, Decimal], delta: Decimal) -> Dict[str, Decimal]:
    if not base_by_category_id:
        return {}
    keys = list(base_by_category_id.keys())
    if len(keys) == 1:
        return {keys[0]: delta}

    total_base = sum(base_by_category_id.values(), Decimal("0"))
    if total_base == 0:
        share = to_cents(delta / Decimal(len(keys)))
        distributed = {key: share for key in keys}
        distributed[keys[-1]] += delta - sum(distributed.values(), Decimal("0"))
        return distributed

    distributed: Dict[str, Decimal] = {}
    running = Decimal("0")
    for key in keys[:-1]:
        share = to_cents(delta * (base_by_category_id[key] / total_base))
        distributed[key] = share
        running += share
    distributed[keys[-1]] = delta - running
    return distributed


def build_split_plan(
    transaction: Dict[str, Any],
    order: Dict[str, Any],
    assignments: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Decimal, Decimal, Decimal]:
    tx_amount = to_cents(to_decimal(transaction.get("amount")) or Decimal("0"))
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
        item_total = line_item_total(item)
        line_total += item_total
        base_by_category[category_id] = base_by_category.get(category_id, Decimal("0")) + item_total
        count_by_category[category_id] = count_by_category.get(category_id, 0) + 1
        category_by_id[category_id] = category

    line_total = to_cents(line_total)
    delta = to_cents(tx_abs - line_total)
    distributed = distribute_delta(base_by_category, delta)

    preview_rows: List[Dict[str, Any]] = []
    split_data: List[Dict[str, Any]] = []
    merchant_name_value = merchant_name(transaction)

    category_ids = list(base_by_category.keys())
    running_signed = Decimal("0")
    for idx, category_id in enumerate(category_ids):
        category = category_by_id[category_id]
        base = to_cents(base_by_category[category_id])
        extra = to_cents(distributed.get(category_id, Decimal("0")))
        split_abs = to_cents(base + extra)
        split_signed = to_cents(split_abs * sign)

        if idx == len(category_ids) - 1:
            split_signed = to_cents(tx_amount - running_signed)
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
        split_data.append(
            {
                "merchantName": merchant_name_value,
                "amount": float(split_signed),
                "categoryId": category_id,
            }
        )

    return split_data, preview_rows, tx_amount, line_total, delta


def print_split_preview(
    preview_rows: List[Dict[str, Any]],
    tx_amount: Decimal,
    line_total: Decimal,
    delta: Decimal,
    order: Dict[str, Any],
    use_color: bool,
) -> None:
    print()
    print(style("Split Preview", bold=True, color="36", use_color=use_color))
    print(
        f"Transaction amount: {format_amount(tx_amount)} | "
        f"Line-item total: {format_amount(line_total)} | "
        f"Distributed remainder (tax/adjustments): {format_amount(delta)}"
    )
    order_tax = to_decimal(order.get("tax"))
    if order_tax is not None:
        print(f"Receipt tax: {format_amount(order_tax)}")

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
    print(style(header, bold=True, color="90", use_color=use_color))
    for row in rows:
        print(
            f"{truncate(row['category'], cat_w):<{cat_w}}  "
            f"{row['items']:>5}  "
            f"{format_amount(row['base']):>12}  "
            f"{format_amount(row['delta']):>12}  "
            f"{format_amount(row['split']):>12}"
        )
    print()


async def maybe_match_retail_transaction(
    mm: Any,
    transaction: Dict[str, Any],
    candidate: Dict[str, Any],
) -> None:
    retail_transactions = candidate.get("retail_transactions") or []
    matching = [rt for rt in retail_transactions if amount_match(rt.get("total"), transaction.get("amount"))]
    if not matching:
        return

    selected = matching[0]
    if ((selected.get("transaction") or {}).get("id")) == transaction.get("id"):
        return

    response = await with_spinner(
        mm.match_retail_transaction(
            retail_transaction_id=selected.get("id"),
            transaction_id=transaction.get("id"),
        )
    )
    errors = ((response or {}).get("matchRetailTransaction") or {}).get("errors") or []
    if errors:
        print(
            "Warning: could not match retail transaction automatically: "
            f"{errors[0].get('message', 'unknown error')}"
        )


async def run_match_flow(mm: Any, review_transactions: List[Dict[str, Any]], use_color: bool) -> int:
    transactions = get_review_transactions_for_match(review_transactions)
    if not transactions:
        print("No eligible transactions. A transaction must need review and not already be split.")
        return 0

    categories = await get_active_categories(mm)
    if not categories:
        raise RuntimeError("No active categories found in Monarch.")

    syncs = await get_retail_syncs(mm)
    candidates_by_tx: Dict[str, List[Dict[str, Any]]] = {}
    for tx in transactions:
        tx_id = str(tx.get("id", ""))
        candidates = build_order_candidates(tx, syncs)
        if candidates:
            candidates_by_tx[tx_id] = candidates

    transactions = [tx for tx in transactions if str(tx.get("id", "")) in candidates_by_tx]
    if not transactions:
        print("No eligible transactions with receipt candidates.")
        return 0

    while True:
        selected_tx = select_transaction_for_match(transactions, use_color=use_color)
        if selected_tx is None:
            return 0
        candidates = candidates_by_tx.get(str(selected_tx.get("id", "")), [])

        while True:
            candidate = select_order_candidate(candidates, transaction=selected_tx, use_color=use_color)
            if candidate == BACK:
                break
            if candidate == QUIT:
                return 0

            order = candidate["order"]
            assignments: Dict[str, Dict[str, Any]] = {}

            while True:
                assignment_result = select_line_items_and_categories(
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

                split_data, preview_rows, tx_amount, line_total, delta = build_split_plan(
                    selected_tx,
                    order,
                    assignments,
                )
                print_split_preview(preview_rows, tx_amount, line_total, delta, order, use_color=use_color)
                prompt_text = style("Create these splits?", color="36", use_color=use_color)
                confirm = prompt(prompt_text + " [Y]/n, b back, q quit: ").strip().lower()
                if confirm == "":
                    confirm = "y"
                if confirm in {"b", "back"}:
                    continue
                if confirm in {"q", "quit"}:
                    return 0
                if confirm not in {"y", "yes"}:
                    print("Invalid choice.")
                    continue

                await maybe_match_retail_transaction(mm, selected_tx, candidate)
                response = await with_spinner(
                    mm.update_transaction_splits(
                        transaction_id=selected_tx.get("id"),
                        split_data=split_data,
                    )
                )
                errors = ((response or {}).get("updateTransactionSplit") or {}).get("errors") or []
                if errors:
                    raise RuntimeError(
                        f"Failed to create splits: {errors[0].get('message', 'unknown error')}"
                    )

                print(style("Split transaction updated successfully.", color="32", bold=True, use_color=use_color))
                return 0
