"""DSH 状态看板：在 QQ 里只读查看 DeepSeek Harness 当前会话进度。

只读设计：不连 DSH 进程、不抢写锁、不产生任何会话写入。
唯一的数据来源是 ~/.dsh/sessions 下的会话日志（zstd + JSONL）。
"""

from __future__ import annotations

import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

from .dsh_status import (
    Status,
    find_latest_session,
    read_status,
    render_card,
    render_done,
)

PLUGIN_NAME = "astrbot_plugin_dsh_status"
KV_WATCH = "watch_targets"
KV_LAST_END = "last_end"
KV_PENDING = "pending_pushes"


@register(PLUGIN_NAME, "dsh-qq-bridge contributors", "DSH 状态看板：只读查看 DeepSeek Harness 会话进度", "0.1.0")
class DshStatusPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self._poll_task: asyncio.Task | None = None
        self._watch: set[str] = set()
        self._last_end: dict[str, int] = {}
        self._pending: list[dict] = []
        self._busy = False

    # ---------------------------------------------------------------- lifecycle

    async def initialize(self) -> None:
        try:
            self._watch = set(await self.get_kv_data(KV_WATCH, []) or [])
            self._last_end = dict(await self.get_kv_data(KV_LAST_END, {}) or {})
            self._pending = list(await self.get_kv_data(KV_PENDING, []) or [])
        except Exception:  # noqa: BLE001 - fresh install has no kv yet
            logger.exception("[dsh-status] 读取推送目标失败，按空处理")
        self._poll_task = asyncio.create_task(self._poll_loop(), name="dsh-status-poll")
        logger.info(
            "[dsh-status] 已启动，监看 %s 个会话目标，待补发 %s 条",
            len(self._watch),
            len(self._pending),
        )

    async def terminate(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    # ------------------------------------------------------------------- config

    def _cfg(self, key: str, default=None):
        try:
            value = self.config.get(key, default) if self.config is not None else default
        except Exception:  # noqa: BLE001 - config may be a plain dict or AstrBotConfig
            value = default
        return default if value is None else value

    def _allowed(self, event: AstrMessageEvent) -> bool:
        if bool(self._cfg("private_only", True)) and not event.is_private_chat():
            return False
        allowed = [str(item).strip() for item in (self._cfg("allowed_uins", []) or [])]
        if not allowed:
            return True
        return str(event.get_sender_id()).strip() in allowed

    def _session_id(self) -> str | None:
        fixed = str(self._cfg("session_id", "") or "").strip()
        return fixed or find_latest_session()

    async def _read(self) -> Status | None:
        session_id = self._session_id()
        if not session_id:
            return None
        try:
            return await asyncio.to_thread(read_status, session_id, self._cfg("node_path") or None)
        except Exception:  # noqa: BLE001 - never let a read failure kill the handler/loop
            logger.exception("[dsh-status] 读取会话状态失败")
            return None

    # ----------------------------------------------------------------- commands

    async def _status_text(self) -> str:
        status = await self._read()
        if status is None:
            logger.info("[dsh-status] /status：没有可用会话")
            return "没找到任何 DSH 会话。先在桌面上开一个再试。"
        logger.info(
            "[dsh-status] /status：%s running=%s turn=%s step=%s",
            status.session_id,
            status.running,
            status.turn,
            status.step,
        )
        card = render_card(status, compact=bool(self._cfg("compact", False)))
        if self._pending:
            card = f"📮 还有 {len(self._pending)} 条完成通知没发出去\n{card}"
        return card

    @filter.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        if not self._allowed(event):
            return
        yield event.plain_result(await self._status_text())

    @filter.command("状态")
    async def cmd_status_cn(self, event: AstrMessageEvent):
        if not self._allowed(event):
            return
        yield event.plain_result(await self._status_text())

    @filter.command("watch")
    async def cmd_watch(self, event: AstrMessageEvent):
        if not self._allowed(event):
            return
        if not bool(self._cfg("watch_enabled", True)):
            yield event.plain_result("推送已在配置里关闭。")
            return
        target = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not target:
            yield event.plain_result("这个会话拿不到推送地址。")
            return
        self._watch.add(target)
        await self.put_kv_data(KV_WATCH, sorted(self._watch))
        status = await self._read()
        name = (status.title or status.session_id) if status else "（还没有会话）"
        yield event.plain_result(f"👀 已开启完成推送：{name}\n跑完/中断时我会在这里说一声。")

    @filter.command("unwatch")
    async def cmd_unwatch(self, event: AstrMessageEvent):
        if not self._allowed(event):
            return
        target = str(getattr(event, "unified_msg_origin", "") or "").strip()
        self._watch.discard(target)
        await self.put_kv_data(KV_WATCH, sorted(self._watch))
        yield event.plain_result("已关闭完成推送。")

    # -------------------------------------------------------------------- push

    async def _poll_loop(self) -> None:
        await asyncio.sleep(5)  # let the host finish booting
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive one bad tick
                logger.exception("[dsh-status] 轮询出错")
            await asyncio.sleep(max(1, int(self._cfg("poll_seconds", 3))))

    async def _tick(self) -> None:
        if not self._watch or self._busy:
            return
        self._busy = True
        try:
            status = await self._read()
            if status is None or not status.ok:
                return
            key = status.session_id
            marker = status.end_marker
            previous = self._last_end.get(key)
            if previous is None:
                # First sighting: remember the current end without announcing an
                # older turn the user already knows about.
                self._last_end[key] = marker
                await self.put_kv_data(KV_LAST_END, self._last_end)
                return
            if marker <= previous:
                return

            message = render_done(status, compact=bool(self._cfg("compact", False)))
            delivered = False
            for target in list(self._watch):
                try:
                    await self.context.send_message(target, MessageChain().message(message))
                    delivered = True
                except Exception as exc:  # noqa: BLE001 - one dead target must not block others
                    logger.warning("[dsh-status] 推送到 %s 失败：%s", target, exc)
            if delivered:
                await self._flush_pending()
            else:
                await self._remember_pending(marker, message)
            self._last_end[key] = marker
            await self.put_kv_data(KV_LAST_END, self._last_end)
        finally:
            self._busy = False

    async def _remember_pending(self, marker: int, text: str) -> None:
        """Keep the notification instead of losing it when every send fails."""
        self._pending.append({"at": marker, "text": text})
        self._pending = self._pending[-5:]
        await self.put_kv_data(KV_PENDING, self._pending)
        logger.warning("[dsh-status] 推送未送达，记为待补发（共 %s 条）", len(self._pending))

    async def _flush_pending(self) -> None:
        """Resend notifications that failed while the transport was down."""
        if not self._pending or not self._watch:
            return
        remaining: list[dict] = []
        for item in self._pending:
            sent = False
            for target in list(self._watch):
                try:
                    await self.context.send_message(
                        target, MessageChain().message(str(item.get("text", "")))
                    )
                    sent = True
                except Exception as exc:  # noqa: BLE001 - keep it queued
                    logger.warning("[dsh-status] 补发失败：%s", exc)
            if not sent:
                remaining.append(item)
        if len(remaining) != len(self._pending):
            logger.info("[dsh-status] 补发完成，剩 %s 条", len(remaining))
        self._pending = remaining
        await self.put_kv_data(KV_PENDING, self._pending)
