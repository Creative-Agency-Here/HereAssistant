"""Authenticated control and contour heartbeat endpoints for Web/VS Code clients."""

from __future__ import annotations

from aiohttp import web

from core import contours, control


async def stop_handler(request: web.Request) -> web.Response:
    request_id = control.request_stop(int(request["user"]["id"]))
    return web.json_response({"accepted": True, "requestId": request_id}, status=202)


async def heartbeat_handler(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        result = contours.heartbeat(int(request["user"]["id"]), payload)
    except contours.ContourError as error:
        return web.json_response({"error": str(error)}, status=400)
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid_json"}, status=400)
    return web.json_response(result)


async def close_handler(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        contour_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
        closed = contours.close(int(request["user"]["id"]), contour_id)
    except contours.ContourError as error:
        return web.json_response({"error": str(error)}, status=400)
    except (ValueError, TypeError):
        return web.json_response({"error": "invalid_json"}, status=400)
    return web.json_response({"closed": closed})
