"""Retry decorator for transient failures against external APIs/DB connections."""

import functools
import time


def with_retries(attempts: int = 3, delay: float = 2, backoff: float = 2):
    """Retry a function on exception, with exponential backoff. Re-raises the
    last exception if every attempt fails."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            wait = delay
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == attempts:
                        raise
                    print(
                        f"[retry] {func.__name__} failed (attempt {attempt}/{attempts}): "
                        f"{e}. Retrying in {wait}s..."
                    )
                    time.sleep(wait)
                    wait *= backoff

        return wrapper

    return decorator
