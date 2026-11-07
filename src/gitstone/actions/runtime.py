"""Glue between an action's submitted values and the core's :class:`Config`."""
import os

from ..core import Config, build_http_headers, parse_lines, parse_proxy
from .spec import RunContext


def build_config(ctx: RunContext, v: dict) -> Config:
    """A :class:`Config` for ``ctx`` from connection values ``v``.
    Raises ValueError with a readable message if they don't hold together."""
    headers = build_http_headers(v["user_agent"], parse_lines(v["headers"]), v["cookie"])

    proxy = v["proxy"] or None
    if proxy:
        parse_proxy(proxy)

    cert = v["client_cert_p12"] or None
    if cert and not os.path.isfile(cert):
        raise ValueError("client certificate %r does not exist" % cert)

    return Config(
        url=ctx.url,
        directory=ctx.directory,
        retry=v["retry"],
        timeout=v["timeout"],
        http_headers=headers,
        client_cert_p12=cert,
        client_cert_p12_password=v["client_cert_p12_password"] or None,
        method=(v["method"] or "GET").upper(),
        any_status=v["any_status"],
        delay=v["delay"],
        proxy=proxy,
        log=ctx.log,
        should_stop=ctx.should_stop,
    )


def local_config(ctx: RunContext) -> Config:
    """A :class:`Config` for steps that never touch the network."""
    return Config(
        url=ctx.url, directory=ctx.directory, log=ctx.log, should_stop=ctx.should_stop
    )
