import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from .constants import CENT


def highlight_line(text: str, color: str, use_color: bool) -> str:
    if not use_color:
        return text
    start = f"\033[{color}m"
    end = "\033[0m"
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return f"{start}{plain}{end}"


def pad_ansi(text: str, width: int, align: str = "left") -> str:
    visible = re.sub(r"\x1b\[[0-9;]*m", "", text)
    pad = max(0, width - len(visible))
    if pad == 0:
        return text
    if align == "right":
        return (" " * pad) + text
    return text + (" " * pad)


def style(text: str, *, color: Optional[str] = None, bold: bool = False, use_color: bool = True) -> str:
    if not use_color:
        return text
    codes = []
    if bold:
        codes.append("1")
    if color:
        codes.append(color)
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


def truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except Exception:
        return None


def to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def format_amount(value: Any) -> str:
    number = to_decimal(value)
    if number is None:
        return str(value)
    return f"${to_cents(abs(number)):,.2f}"


def format_tx_amount(value: Any, use_color: bool, resume_color: Optional[str] = None) -> str:
    number = to_decimal(value)
    if number is None:
        return format_amount(value)
    amount = format_amount(number)
    if use_color and number > 0 and resume_color is None:
        return style(amount, color="32", use_color=use_color)
    return amount
