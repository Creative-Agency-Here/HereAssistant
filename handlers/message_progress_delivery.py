"""Telegram boundary для progress-сообщения с адаптивным throttling."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message

from .message_progress import (
    ProgressState,
    activate_quiet_mode,
    apply_flood_backoff,
    can_push_progress,
    record_push_success,
)


@dataclass(frozen=True, slots=True)
class ProgressDeliveryPolicy:
    enabled: bool
    started_at: float
    base_interval: float
    max_interval: float
    backoff_factor: float
    reset_successes: int
    quiet_after: float
    quiet_interval: float


class ProgressDelivery:
    def __init__(
        self,
        *,
        state: ProgressState,
        source_message: Message,
        render: Callable[[], str],
        policy: ProgressDeliveryPolicy,
        logger: logging.Logger,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.state = state
        self.source_message = source_message
        self.render = render
        self.policy = policy
        self.logger = logger
        self.clock = clock

    async def push(self, *, force: bool = False) -> None:
        if not self.policy.enabled:
            return
        now = self.clock()
        if activate_quiet_mode(
            self.state,
            now=now,
            started_at=self.policy.started_at,
            after_seconds=self.policy.quiet_after,
        ):
            self.logger.info("progress: quiet mode after %.0fs", now - self.policy.started_at)

        if not can_push_progress(
            self.state,
            now=now,
            quiet_interval=self.policy.quiet_interval,
            force=force,
        ):
            return

        display = self.render()
        if display == self.state.last_displayed:
            return
        try:
            if self.state.message is None:
                self.state.message = await self.source_message.answer(display, parse_mode="HTML")
            else:
                await self.state.message.edit_text(display, parse_mode="HTML")
            self.state.last_displayed = display
            self.state.last_edit_ts = now
            record_push_success(
                self.state,
                base_interval=self.policy.base_interval,
                factor=self.policy.backoff_factor,
                reset_after=self.policy.reset_successes,
            )
        except TelegramRetryAfter as error:
            self._bump_backoff(float(error.retry_after or 30))
        except TelegramBadRequest as error:
            self._handle_bad_request(error)
        except Exception as error:
            # Boundary catch: aiogram/network adapters may surface third-party
            # exceptions. Flood-like errors are classified; all others are logged.
            wait = _legacy_retry_after(error)
            if wait is not None:
                self._bump_backoff(wait)
            else:
                self.logger.warning("progress edit unexpected error: %s", error)

    def _handle_bad_request(self, error: TelegramBadRequest) -> None:
        message = str(error).lower()
        if "not modified" in message:
            return
        wait = _legacy_retry_after(error)
        if wait is not None:
            self._bump_backoff(wait)
        else:
            self.logger.warning("progress edit failed: %s", error)

    def _bump_backoff(self, wait_seconds: float) -> None:
        apply_flood_backoff(
            self.state,
            now=self.clock(),
            wait_seconds=wait_seconds,
            factor=self.policy.backoff_factor,
            max_interval=self.policy.max_interval,
        )
        self.logger.warning(
            "progress: FloodWait %.0fs → min_interval=%.1fs",
            wait_seconds,
            self.state.min_interval,
        )


def _legacy_retry_after(error: BaseException) -> float | None:
    message = str(error).lower()
    if not any(
        marker in message for marker in ("too many requests", "flood control", "retry after")
    ):
        return None
    match = re.search(r"retry.*?(\d+)", message)
    return float(match.group(1)) if match else 60.0
