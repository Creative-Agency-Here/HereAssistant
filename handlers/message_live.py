"""Lifecycle typing, rich draft и progress для одного provider-запроса."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import Message

from utils import rich

from .message_progress import ProgressRenderContext, ProgressState, render_progress
from .message_progress_delivery import ProgressDelivery, ProgressDeliveryPolicy


@dataclass(frozen=True, slots=True)
class LiveSessionPolicy:
    progress_enabled: bool
    progress_min_interval: float
    progress_max_interval: float
    progress_backoff_factor: float
    progress_reset_successes: int
    progress_quiet_after: float
    progress_quiet_interval: float
    progress_chain_limit: int
    progress_max_chars: int
    progress_heartbeat_interval: float
    progress_heartbeat_idle: float
    typing_interval: float
    draft_min_interval: float


@dataclass(slots=True)
class DraftState:
    enabled: bool
    draft_id: int
    last_sent_at: float = 0.0


class MessageLiveSession:
    def __init__(
        self,
        *,
        bot: Bot,
        source_message: Message,
        model: str | None,
        account_label: str | None,
        account_notes: str | None,
        attachments: Sequence[Path],
        started_at: float,
        rich_stream_enabled: bool,
        policy: LiveSessionPolicy,
        logger: logging.Logger,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.bot = bot
        self.source_message = source_message
        self.chat_id = source_message.chat.id
        self.thread_id = source_message.message_thread_id or 0
        self.started_at = started_at
        self.policy = policy
        self.logger = logger
        self.clock = clock
        self.state = ProgressState(
            last_event_ts=clock(),
            min_interval=policy.progress_min_interval,
            attachments=list(attachments),
        )
        self.render_context = ProgressRenderContext(
            model=model,
            account_label=account_label,
            account_notes=account_notes,
            started_at=started_at,
            chain_limit=policy.progress_chain_limit,
            max_partial_chars=policy.progress_max_chars,
            draft_enabled=rich_stream_enabled,
        )
        self.draft = DraftState(
            enabled=rich_stream_enabled,
            draft_id=(int(started_at * 1000) % 2_000_000_000) or 1,
        )
        self.progress_delivery = ProgressDelivery(
            state=self.state,
            source_message=source_message,
            render=self._render,
            policy=ProgressDeliveryPolicy(
                enabled=policy.progress_enabled,
                started_at=started_at,
                base_interval=policy.progress_min_interval,
                max_interval=policy.progress_max_interval,
                backoff_factor=policy.progress_backoff_factor,
                reset_successes=policy.progress_reset_successes,
                quiet_after=policy.progress_quiet_after,
                quiet_interval=policy.progress_quiet_interval,
            ),
            logger=logger,
            clock=clock,
        )
        self._typing_stop = asyncio.Event()
        self._heartbeat_stop = asyncio.Event()
        self._typing_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._typing_task is None:
            self._typing_task = asyncio.create_task(self._typing_heartbeat())
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._progress_heartbeat())
        await self.progress_delivery.push(force=True)

    async def progress_callback(
        self,
        partial_text: str,
        event_type: str,
        meta: Mapping[str, Any],
    ) -> None:
        if self.state.overflowed:
            return
        self.state.last_partial = partial_text
        if meta:
            self.state.last_meta = meta
        now = self.clock()
        self.state.last_event_ts = now
        if (
            self.draft.enabled
            and partial_text
            and event_type in ("assistant_delta", "partial_delta")
            and now - self.draft.last_sent_at >= self.policy.draft_min_interval
        ):
            self.draft.last_sent_at = now
            sent = await rich.send_draft(
                self.bot,
                self.chat_id,
                self.draft.draft_id,
                partial_text + " ▍",
                self.thread_id or None,
            )
            if not sent:
                self.draft.enabled = False
        force = event_type in ("tool_use", "tool_start", "tool_result")
        await self.progress_delivery.push(force=force)

    async def stop_progress(self) -> None:
        self._heartbeat_stop.set()
        task = self._heartbeat_task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(task, timeout=2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    async def close(self) -> None:
        await self.stop_progress()
        self._typing_stop.set()
        task = self._typing_task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(task, timeout=1)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    def _render(self) -> str:
        context = ProgressRenderContext(
            model=self.render_context.model,
            account_label=self.render_context.account_label,
            account_notes=self.render_context.account_notes,
            started_at=self.render_context.started_at,
            chain_limit=self.render_context.chain_limit,
            max_partial_chars=self.render_context.max_partial_chars,
            draft_enabled=self.draft.enabled,
        )
        rendered = render_progress(self.state, context, now=self.clock())
        self.state.overflowed = self.state.overflowed or rendered.overflowed
        return rendered.html

    async def _typing_heartbeat(self) -> None:
        try:
            while not self._typing_stop.is_set():
                try:
                    await self.bot.send_chat_action(self.chat_id, "typing")
                except Exception as error:
                    # Typing is cosmetic and must never cancel provider execution.
                    self.logger.debug("typing heartbeat failed: %s", error)
                try:
                    await asyncio.wait_for(
                        self._typing_stop.wait(), timeout=self.policy.typing_interval
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _progress_heartbeat(self) -> None:
        try:
            while not self._heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(
                        self._heartbeat_stop.wait(),
                        timeout=self.policy.progress_heartbeat_interval,
                    )
                    break
                except asyncio.TimeoutError:
                    pass
                if self.state.message is None or self.state.overflowed:
                    continue
                if self.clock() - self.state.last_event_ts > self.policy.progress_heartbeat_idle:
                    continue
                await self.progress_delivery.push(force=False)
        except asyncio.CancelledError:
            pass
