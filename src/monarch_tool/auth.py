import getpass
import os
from typing import Any, List

from monarch import LoginFailedException, Monarch, RequestFailedException, RequireMFAException

from .terminal import prompt, with_spinner


def is_authentication_error(exc: Exception) -> bool:
    auth_markers = [
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "not authenticated",
        "not authorized",
        "authentication",
        "invalid token",
        "expired token",
        "token is invalid",
    ]
    queue: List[Any] = [exc]
    seen: set[int] = set()

    while queue:
        current = queue.pop(0)
        if current is None:
            continue
        obj_id = id(current)
        if obj_id in seen:
            continue
        seen.add(obj_id)

        if isinstance(current, LoginFailedException):
            return True
        if isinstance(current, RequestFailedException):
            msg = str(current).lower()
            if any(marker in msg for marker in auth_markers):
                return True

        msg = str(current).lower()
        if any(marker in msg for marker in auth_markers):
            return True

        queue.append(getattr(current, "__cause__", None))
        queue.append(getattr(current, "__context__", None))

    return False


async def authenticate() -> Monarch:
    mm = Monarch()
    session_path = mm._session_file
    if os.path.exists(session_path):
        try:
            mm.load_session(session_path)
            await with_spinner(mm.get_accounts())
            return mm
        except Exception as exc:
            if is_authentication_error(exc):
                print("Saved session is no longer valid. Re-authentication required.")
                try:
                    mm.delete_session(session_path)
                except Exception:
                    pass
            else:
                print(
                    "Warning: could not verify saved session due to a non-auth error. "
                    "Continuing with saved session."
                )
                return mm

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
            return mm

    email = prompt("Monarch email: ")
    password = getpass.getpass("Monarch password: ")
    if not email or not password:
        raise RuntimeError("Email and password are required.")

    return await _login_with_prompts(email, password)
