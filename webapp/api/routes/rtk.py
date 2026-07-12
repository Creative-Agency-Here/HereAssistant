"""GET /api/rtk — агрегированная экономия RTK текущего пользователя."""

from aiohttp import web

from core import rtk


async def handler(request: web.Request) -> web.Response:
    return web.json_response(rtk.user_savings(request["user"]["id"]))
