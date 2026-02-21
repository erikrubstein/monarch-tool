import getpass
import os
from monarch import Monarch, RequireMFAException

from .terminal import prompt, with_spinner


async def authenticate() -> Monarch:
    mm = Monarch()
    session_path = mm._session_file
    if os.path.exists(session_path):
        try:
            mm.load_session(session_path)
            await with_spinner(mm.get_accounts())
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
