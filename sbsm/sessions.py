"""Short-lived signed browser sessions. Bearer API authentication remains supported."""
import base64
import hashlib
import hmac
import json
import secrets

COOKIE = "sbsm_session"


class BrowserSessions:
    def __init__(self, secret, clock, seconds=3600):
        self.key = hmac.digest(secret.encode(), b"sbsm-browser-session-v1", "sha256")
        self.clock = clock
        self.seconds = seconds

    def signature(self, payload):
        return hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()

    def csrf(self, cookie):
        return hmac.new(self.key, ("csrf:" + cookie).encode(), hashlib.sha256).hexdigest()

    def issue(self):
        claims = {"expires_ms": self.clock() + self.seconds * 1000, "nonce": secrets.token_hex(16)}
        encoded = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).decode().rstrip("=")
        cookie = encoded + "." + self.signature(encoded)
        return cookie, {"authenticated": True, "expires_ms": claims["expires_ms"], "csrf_token": self.csrf(cookie)}

    def verify(self, cookie):
        if not cookie or len(cookie) > 512:
            return None
        try:
            encoded, signature = cookie.split(".")
            if not hmac.compare_digest(signature, self.signature(encoded)):
                return None
            claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if type(claims["expires_ms"]) is not int or claims["expires_ms"] <= self.clock():
                return None
            if claims["expires_ms"] > self.clock() + self.seconds * 1000:
                return None
            return {"authenticated": True, "expires_ms": claims["expires_ms"], "csrf_token": self.csrf(cookie)}
        except (ValueError, TypeError, KeyError):
            return None
