from slowapi import Limiter
from slowapi.util import get_remote_address
from .config import get_settings

limiter = Limiter(key_func=get_remote_address)
auth_limit = lambda: get_settings().auth_rate_limit    # noqa: E731
chat_limit = lambda: get_settings().chat_rate_limit    # noqa: E731
ai_test_limit = lambda: get_settings().ai_test_rate_limit    # noqa: E731
test_call_limit = lambda: get_settings().test_call_rate_limit    # noqa: E731
