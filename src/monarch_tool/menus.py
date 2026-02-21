import shutil
from typing import Any, List, Optional

from .constants import BACK, QUIT
from .formatting import highlight_line, style
from .terminal import clear_screen, hidden_cursor, read_key


def choose_single_tui(
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

    with hidden_cursor():
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
            lines.append(style(title, bold=True, color="36", use_color=use_color))
            if subtitle_lines:
                lines.extend(subtitle_lines)
            if searchable:
                lines.append(f"Search: {query or '(type to filter)'}")
            if message:
                lines.append(style(message, color="33", use_color=use_color))
            lines.append(instructions or "\u2191/\u2193 move, Enter select, b back, q quit")
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
                        line = highlight_line(line, color="33", use_color=use_color)
                    lines.append(line)
            else:
                lines.append("  (no matches)")

            clear_screen()
            print("\n".join(lines))

            key = read_key()
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
