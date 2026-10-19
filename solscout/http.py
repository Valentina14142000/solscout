from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .models import SourceError

UA = "solscout/0.1 (+https://github.com/Valentina14142000)"


def request_json(url: str, *, body: dict | None = None, headers: dict | None = None,
                 timeout: float = 12.0, retries: int = 2):
    """GET (or POST when `body` is given) and decode JSON. Retries 429/5xx with a short backoff."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    last = "unknown error"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(4.0, 1.0 * (attempt + 1)))
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"network: {getattr(e, 'reason', e)}"
            if attempt < retries:
                time.sleep(0.5)
                continue
            break
        except ValueError:
            last = "response was not valid JSON"
            break
    host = url.split("/")[2] if "//" in url else url
    raise SourceError(f"{host}: {last}")
