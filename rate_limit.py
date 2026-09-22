from slowapi import Limiter
from slowapi.util import get_remote_address


def device_key_or_ip(request):
    """
    Rate-limit per DEVICE, not per IP. Multiple vehicles could share the
    same cellular gateway IP, so limiting by IP alone could unfairly
    throttle one vehicle because of another's traffic.
    Falls back to IP address for routes with no device key (e.g. registration).
    """
    device_key = request.headers.get("x-device-key")
    return device_key if device_key else get_remote_address(request)


limiter = Limiter(key_func=device_key_or_ip)
