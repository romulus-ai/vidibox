"""URL-Normalisierung fuer Quellen, die yt-dlp nicht direkt kennt.

schule.zdf.de ist eine eigene Oberflaeche ueber der ZDF-Mediathek; die Videos
liegen unter demselben Slug auf www.zdf.de, das yt-dlp unterstuetzt.
"""

import re
from urllib.parse import urlsplit, urlunsplit

# (Host-Muster, Ersatz-Host, Query verwerfen?)
_REWRITES: list[tuple[re.Pattern[str], str, bool]] = [
    # https://schule.zdf.de/video/<slug>  ->  https://www.zdf.de/video/<slug>
    (re.compile(r"^schule\.zdf\.de$", re.I), "www.zdf.de", True),
]


def normalize_source_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    query = parts.query
    for pattern, replacement, drop_query in _REWRITES:
        if pattern.match(host):
            host = replacement
            if drop_query:
                query = ""
            break
    return urlunsplit((parts.scheme or "https", host, parts.path, query, ""))
