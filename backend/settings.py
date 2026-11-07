"""Environment limits shared by Windows development and ARM deployment."""
import os


def integer(name: str, default: int, minimum: int=1, maximum: int=1_000_000_000) -> int:
    raw=os.environ.get(name,str(default))
    try:value=int(raw)
    except ValueError as exc:raise ValueError(f'{name} must be an integer') from exc
    if not minimum<=value<=maximum:raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return value
