import asyncio
from contextlib import contextmanager
from itertools import cycle
import os
import sys
from typing import Any, Awaitable


def enable_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def supports_color() -> bool:
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None


async def with_spinner(awaitable: Awaitable[Any]) -> Any:
    if not sys.stdout.isatty():
        return await awaitable
    frames = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827", "\u2807", "\u280f"]
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


def prompt(message: str) -> str:
    try:
        return input(message).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def tui_enabled() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


@contextmanager
def hidden_cursor() -> Any:
    if not tui_enabled():
        yield
        return
    try:
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
        yield
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def read_key() -> str:
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
