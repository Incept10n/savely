import functools

from flask import current_app, request


def get_auth():
    header = request.headers.get("X-Auth-String", "")
    return header.strip()


def check_auth(auth_value):
    expected = current_app.config["AUTH_STRING"]
    return bool(auth_value) and auth_value == expected


def require_auth(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not check_auth(get_auth()):
            return {"error": "unauthorized"}, 401
        return fn(*args, **kwargs)

    return wrapper
