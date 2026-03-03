import getpass
import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from monarch import Monarch, RequireMFAException

from .auth import is_authentication_error
from .constants import BACK, QUIT
from .formatting import pad_ansi, style, truncate
from .menus import choose_single_tui
from .terminal import prompt, tui_enabled, with_spinner

SYNC_ACCOUNTS_FILE = os.path.join(".mm", "receipt_sync_accounts.json")
SYNC_SESSIONS_DIR = os.path.join(".mm", "receipt_sync_sessions")
MONARCH_PROVIDER = "monarch"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _source_label(account: Dict[str, Any]) -> str:
    label = str(account.get("label") or "").strip()
    email = str(account.get("email") or "").strip()
    if label:
        return label
    if email:
        return email
    return f"{account.get('provider', MONARCH_PROVIDER)}:{account.get('id', '?')}"


def _normalize_account(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None

    account_id = str(raw.get("id") or "").strip()
    provider = str(raw.get("provider") or MONARCH_PROVIDER).strip().lower()
    session_file = str(raw.get("session_file") or "").strip()
    if not account_id or not provider or not session_file:
        return None

    normalized = {
        "id": account_id,
        "provider": provider,
        "label": str(raw.get("label") or "").strip(),
        "email": str(raw.get("email") or "").strip(),
        "session_file": session_file,
        "created_at": str(raw.get("created_at") or "").strip() or _utc_now_iso(),
        "updated_at": str(raw.get("updated_at") or "").strip() or _utc_now_iso(),
    }
    return normalized


def load_receipt_sync_accounts() -> List[Dict[str, Any]]:
    if not os.path.exists(SYNC_ACCOUNTS_FILE):
        return []
    try:
        with open(SYNC_ACCOUNTS_FILE, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        print(f"Warning: could not read {SYNC_ACCOUNTS_FILE}.")
        return []

    raw_accounts = payload.get("accounts", []) if isinstance(payload, dict) else []
    accounts: List[Dict[str, Any]] = []
    for raw in raw_accounts:
        account = _normalize_account(raw)
        if account:
            accounts.append(account)
    return accounts


def save_receipt_sync_accounts(accounts: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(SYNC_ACCOUNTS_FILE), exist_ok=True)
    payload = {"accounts": accounts}
    with open(SYNC_ACCOUNTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _session_status(account: Dict[str, Any]) -> Tuple[str, str]:
    session_file = str(account.get("session_file") or "")
    if session_file and os.path.exists(session_file):
        return "session ok", "32"
    return "session missing", "31"


def _format_account_row(
    account: Dict[str, Any],
    use_color: bool,
    *,
    include_index: Optional[int] = None,
) -> str:
    account_id = str(account.get("id") or "?")
    provider = str(account.get("provider") or MONARCH_PROVIDER)
    label = truncate(_source_label(account), 28)
    email = truncate(str(account.get("email") or "-"), 30)
    status_text, status_color = _session_status(account)
    status_value = style(status_text, color=status_color, use_color=use_color)
    provider_value = truncate(provider, 10)
    id_value = truncate(account_id, 10)

    row = (
        f"{pad_ansi(id_value, 10)}  "
        f"{pad_ansi(provider_value, 10)}  "
        f"{pad_ansi(label, 28)}  "
        f"{pad_ansi(email, 30)}  "
        f"{status_value}"
    )
    if include_index is None:
        return row
    return f"[{include_index:>2}] {row}"


def _print_receipt_sync_accounts_table(accounts: List[Dict[str, Any]], use_color: bool) -> None:
    columns = shutil.get_terminal_size(fallback=(120, 20)).columns
    divider = "-" * min(columns, 120)
    print(style("Receipt Sync Accounts", bold=True, color="36", use_color=use_color))
    print(divider)
    header = (
        f"{pad_ansi('ID', 10)}  "
        f"{pad_ansi('Provider', 10)}  "
        f"{pad_ansi('Label', 28)}  "
        f"{pad_ansi('Email', 30)}  "
        "Session"
    )
    print(style(header, bold=True, color="90", use_color=use_color))
    for index, account in enumerate(accounts, start=1):
        print(_format_account_row(account, use_color=use_color, include_index=index))


def list_receipt_sync_accounts(use_color: bool) -> int:
    accounts = load_receipt_sync_accounts()
    if not accounts:
        print(style("No receipt sync accounts configured.", color="33", use_color=use_color))
        print("Use --connect-receipt-account to add one.")
        return 0

    _print_receipt_sync_accounts_table(accounts, use_color=use_color)
    return 0


def _remove_receipt_sync_account_by_id(account_id: str) -> bool:
    target = account_id.strip().lower()
    if not target:
        return False

    accounts = load_receipt_sync_accounts()
    keep: List[Dict[str, Any]] = []
    removed: Optional[Dict[str, Any]] = None

    for account in accounts:
        if str(account.get("id", "")).lower() == target:
            removed = account
            continue
        keep.append(account)

    if removed is None:
        return False

    save_receipt_sync_accounts(keep)
    session_file = str(removed.get("session_file") or "")
    if session_file and os.path.exists(session_file):
        try:
            os.remove(session_file)
        except Exception:
            print(f"Warning: failed to remove session file {session_file}.")
    return True


def _prompt_yes_no(question: str, default: bool = False) -> bool:
    hint = "[Y]/n" if default else "y/[N]"
    while True:
        raw = prompt(f"{question} {hint}: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Invalid choice.")


def _select_receipt_sync_account(accounts: List[Dict[str, Any]], use_color: bool) -> Any:
    if not accounts:
        return BACK

    if tui_enabled():
        options = [_format_account_row(account, use_color=use_color) for account in accounts]
        selection = choose_single_tui(
            title="Remove Receipt Sync Account",
            subtitle_lines=["Choose an account to remove."],
            options=options,
            use_color=use_color,
            marker_mode="cursor",
            instructions="\u2191/\u2193 move, Enter select, b/esc back, q quit",
        )
        if selection in {BACK, QUIT}:
            return selection
        return accounts[selection]

    _print_receipt_sync_accounts_table(accounts, use_color=use_color)
    while True:
        raw = prompt("\nSelect account #, b to go back, q to quit: ").strip().lower()
        if raw in {"b", "back"}:
            return BACK
        if raw in {"q", "quit"}:
            return QUIT
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(accounts):
                return accounts[choice - 1]
        print("Invalid selection.")


def remove_receipt_sync_account(use_color: bool) -> int:
    accounts = load_receipt_sync_accounts()
    if not accounts:
        print(style("No receipt sync accounts configured.", color="33", use_color=use_color))
        print("Use --connect-receipt-account to add one.")
        return 0

    selected = _select_receipt_sync_account(accounts, use_color=use_color)
    if selected in {BACK, QUIT}:
        return 0

    label = _source_label(selected)
    account_id = str(selected.get("id") or "")
    if not _prompt_yes_no(
        style(f"Remove receipt sync account '{label}'?", color="36", use_color=use_color),
        default=False,
    ):
        print("Removal canceled.")
        return 0

    removed = _remove_receipt_sync_account_by_id(account_id)
    if not removed:
        print(style("Failed to remove receipt sync account.", color="31", use_color=use_color))
        return 1

    print(style(f"Removed receipt sync account '{label}'.", color="32", bold=True, use_color=use_color))
    return 0


async def _login_monarch_account(mm: Monarch, email: str, password: str) -> None:
    try:
        await mm.login(
            email=email,
            password=password,
            use_saved_session=False,
            save_session=True,
        )
        return
    except RequireMFAException:
        mfa_code = prompt("Two-factor code: ")
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


async def connect_receipt_sync_account(use_color: bool) -> int:
    print(style("Connect Receipt Sync Account", bold=True, color="36", use_color=use_color))
    provider = prompt("Receipt sync provider [monarch]: ").strip().lower() or MONARCH_PROVIDER
    if provider != MONARCH_PROVIDER:
        print(style(f"Unsupported provider '{provider}'. Supported providers: monarch", color="31", use_color=use_color))
        return 1

    email = prompt("Monarch email for receipt sync account: ")
    password = getpass.getpass("Monarch password: ")
    label = prompt("Label for this sync account (optional): ")
    if not email or not password:
        raise RuntimeError("Email and password are required.")

    accounts = load_receipt_sync_accounts()
    email_key = email.lower()
    existing = next(
        (
            account
            for account in accounts
            if account.get("provider") == MONARCH_PROVIDER and str(account.get("email") or "").lower() == email_key
        ),
        None,
    )

    if existing:
        account_id = str(existing.get("id"))
        session_file = str(existing.get("session_file"))
    else:
        account_id = uuid4().hex[:10]
        os.makedirs(SYNC_SESSIONS_DIR, exist_ok=True)
        session_file = os.path.join(SYNC_SESSIONS_DIR, f"{MONARCH_PROVIDER}_{account_id}.pickle")

    mm = Monarch(session_file=session_file)
    await _login_monarch_account(mm, email, password)
    await with_spinner(mm.get_accounts())

    now = _utc_now_iso()
    if existing:
        existing["label"] = label.strip()
        existing["email"] = email
        existing["updated_at"] = now
        print(
            style(
                f"Updated receipt sync account [{account_id}] {_source_label(existing)}.",
                color="32",
                bold=True,
                use_color=use_color,
            )
        )
    else:
        account = {
            "id": account_id,
            "provider": MONARCH_PROVIDER,
            "label": label.strip(),
            "email": email,
            "session_file": session_file,
            "created_at": now,
            "updated_at": now,
        }
        accounts.append(account)
        print(
            style(
                f"Added receipt sync account [{account_id}] {_source_label(account)}.",
                color="32",
                bold=True,
                use_color=use_color,
            )
        )

    save_receipt_sync_accounts(accounts)
    return 0


async def load_receipt_sync_sources(primary_mm: Any) -> List[Dict[str, Any]]:
    accounts = load_receipt_sync_accounts()
    if not accounts:
        return [
            {
                "id": "primary",
                "provider": MONARCH_PROVIDER,
                "label": "Primary CLI account",
                "mm": primary_mm,
                "is_primary": True,
            }
        ]

    sources: List[Dict[str, Any]] = []
    for account in accounts:
        provider = account.get("provider")
        if provider != MONARCH_PROVIDER:
            print(f"Warning: skipping unsupported receipt sync provider '{provider}' for {account.get('id')}.")
            continue

        session_file = str(account.get("session_file") or "")
        if not session_file or not os.path.exists(session_file):
            print(f"Warning: missing session for receipt sync account [{account.get('id')}].")
            continue

        mm = Monarch(session_file=session_file)
        try:
            mm.load_session(session_file)
            await with_spinner(mm.get_accounts())
        except Exception as exc:
            if is_authentication_error(exc):
                print(
                    f"Warning: receipt sync account [{account.get('id')}] session is invalid. "
                    "Reconnect with --connect-receipt-account."
                )
                continue
            print(
                f"Warning: could not verify receipt sync account [{account.get('id')}] due to a non-auth error. "
                "Using saved session."
            )

        sources.append(
            {
                "id": account.get("id"),
                "provider": provider,
                "label": _source_label(account),
                "mm": mm,
                "is_primary": False,
            }
        )

    if sources:
        return sources

    print("No valid receipt sync accounts found. Using primary CLI account.")
    return [
        {
            "id": "primary",
            "provider": MONARCH_PROVIDER,
            "label": "Primary CLI account",
            "mm": primary_mm,
            "is_primary": True,
        }
    ]
