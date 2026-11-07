"""HTTP plumbing: sessions, retrying requests, and response validation."""
import time
import urllib.parse

import bs4
import requests
import urllib3
from requests_pkcs12 import Pkcs12Adapter

from .paths import is_safe_path
from .proxy import requests_proxies

RETRY_STATUSES = (429, 503)  # "slow down" responses worth waiting out
MAX_BACKOFF = 30  # seconds


def is_html(response):
    """Return True if the response is a HTML webpage."""
    return "text/html" in response.headers.get("Content-Type", "")


def get_indexed_files(response):
    """Return all the files in a directory-index webpage."""
    html = bs4.BeautifulSoup(response.text, "html.parser")
    files = []

    for link in html.find_all("a"):
        url = urllib.parse.urlparse(link.get("href"))

        if (
            url.path
            and is_safe_path(url.path)
            and not url.scheme
            and not url.netloc
        ):
            files.append(url.path)

    return files


def verify_response(response, any_status=False):
    """``(True, "")`` if the response is real content, else ``(False, why)``."""
    if not any_status and response.status_code != 200:
        return False, "responded with status code %d" % response.status_code
    if response.headers.get("Content-Length", "").strip() == "0":
        return False, "responded with a zero-length body"
    if is_html(response):
        return False, "responded with HTML"
    return True, ""


def make_session(cfg):
    # Same as max_retries=<int>, except urllib3 must not sleep for however long
    # a Retry-After header says: request() does that, with a cap.
    retries = urllib3.util.Retry(
        total=cfg.retry, read=False, respect_retry_after_header=False
    )
    # The targets are what we are probing; their certificates are not trusted
    # input, and self-signed hosts are common. Don't spam a warning per request.
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.verify = False
    session.headers = dict(cfg.http_headers)
    session.proxies = requests_proxies(cfg.proxy)

    if cfg.client_cert_p12:
        adapter = Pkcs12Adapter(
            pkcs12_filename=cfg.client_cert_p12,
            pkcs12_password=cfg.client_cert_p12_password,
            max_retries=retries,
        )
    else:
        adapter = requests.adapters.HTTPAdapter(max_retries=retries)

    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def backoff_seconds(response, attempt):
    """How long to wait after a 429/503: Retry-After if the server gave
    a number of seconds, otherwise exponential, always capped."""
    try:
        wait = float(response.headers.get("Retry-After", ""))
    except ValueError:
        wait = 0.5 * (2 ** attempt)

    return max(0.0, min(wait, MAX_BACKOFF))


def request(session, cfg, url, **kwargs):
    """Send one request, honouring ``delay`` and backing off on 429/503."""
    kwargs.setdefault("allow_redirects", False)
    kwargs.setdefault("timeout", cfg.timeout)

    for attempt in range(cfg.retry):
        if cfg.delay:
            time.sleep(cfg.delay)

        response = session.request(cfg.method, url, **kwargs)
        last_attempt = attempt == cfg.retry - 1

        # With any_status the caller wants these responses as they are.
        if (
            response.status_code not in RETRY_STATUSES
            or last_attempt
            or cfg.any_status
        ):
            return response

        wait = backoff_seconds(response, attempt)
        response.close()
        cfg.log.info(
            "[-] %s answered %d, waiting %.1fs before retrying\n",
            url,
            response.status_code,
            wait,
        )
        time.sleep(wait)
