import logging
from types import SimpleNamespace
from typing import cast

from aiohttp import web

from webapp.api.server import RedactedAccessLogger


def test_access_log_does_not_include_query_secrets(caplog) -> None:
    logger = logging.getLogger("test.safe-access")
    access_logger = RedactedAccessLogger(logger, "")
    request = cast(
        web.Request,
        SimpleNamespace(
            remote="127.0.0.1",
            method="GET",
            path="/api/git/oauth/callback/gitea",
            query_string="code=oauth-secret&state=state-secret",
            raw_path="/api/git/oauth/callback/gitea?code=oauth-secret&state=state-secret",
        ),
    )
    response = cast(web.StreamResponse, SimpleNamespace(status=302, body_length=0))

    with caplog.at_level(logging.INFO, logger=logger.name):
        access_logger.log(request, response, 0.125)

    output = caplog.text
    assert "GET /api/git/oauth/callback/gitea" in output
    assert "oauth-secret" not in output
    assert "state-secret" not in output
    assert "query_string" not in output
