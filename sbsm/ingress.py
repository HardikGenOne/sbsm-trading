"""Resolve webhook source through the explicitly selected hosting boundary."""
import ipaddress


def client_ip(request, source):
    if source == "render_cloudflare":
        # Render documents that its public edge overwrites this header.
        # Do not use this mode behind an arbitrary proxy or on a local server.
        values = request.headers.getlist("cf-connecting-ip")
        address = values[0] if len(values) == 1 else ""
    else:
        address = request.client.host if request.client else ""
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return None
