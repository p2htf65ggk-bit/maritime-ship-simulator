# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations


WEBENGINE_HTTP_CACHE_BYTES = 1024 * 1024 * 1024
_offline = False
_interceptors = []


def set_webengine_offline(enabled: bool) -> None:
    global _offline
    _offline = bool(enabled)


def _network_allowed(url) -> bool:
    return not _offline or url.scheme() not in {"http", "https", "ws", "wss"} or url.host() in {"127.0.0.1", "localhost", "::1"}


def configure_webengine_profile(page) -> None:
    """Apply one bounded persistent cache policy to application web views."""
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineUrlRequestInterceptor

    profile = page.profile()
    profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
    profile.setHttpCacheMaximumSize(WEBENGINE_HTTP_CACHE_BYTES)
    if isinstance(profile, QWebEngineProfile) and not any(existing is profile for existing, _ in _interceptors):
        class OfflineInterceptor(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info):
                if not _network_allowed(info.requestUrl()):
                    info.block(True)
        interceptor = OfflineInterceptor(profile)
        profile.setUrlRequestInterceptor(interceptor)
        _interceptors.append((profile, interceptor))
