"""灵枢 Agent 平台 —— 定时触发器调度线程。

🎯 为什么是进程内线程而不是 Celery beat：
    Celery 是软依赖，默认关闭；定时触发是触发器的一半能力，不能因为没开 Celery 就用不了。
    进程内线程零配置就能跑，多副本部署时靠 `claim_trigger` 的行级条件更新保证只有一个副本
    真正执行——这比引入 beat + 独立队列的部署成本低得多。

局限（有意接受）：进程挂掉时在途的定时触发会等到下次启动才补跑，不会丢记录也不会重复跑。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_stop = threading.Event()
_thread: threading.Thread | None = None


def _tick() -> int:
    """扫一轮到期的定时触发器，返回实际执行数量。"""
    from core.db.session import SessionLocal
    from core.services.triggers import claim_trigger, due_triggers, run_trigger

    executed = 0
    with SessionLocal() as db:
        for trigger in due_triggers(db):
            if not claim_trigger(db, trigger):
                continue
            try:
                result = run_trigger(db, trigger, {"trigger_name": trigger.name})
                executed += 1
                logger.info("Trigger %s fired (session=%s run=%s)", trigger.id, result["session_id"], result["run_id"])
            except Exception:
                # 单条失败不影响本轮其它触发器；last_fired_at 不更新，下一轮按新时间再试
                db.rollback()
                logger.exception("Trigger %s failed to run", trigger.id)
    return executed


def _loop(interval: int) -> None:
    while not _stop.wait(interval):
        try:
            _tick()
        except Exception:
            logger.exception("Trigger scheduler tick failed")


def start_trigger_scheduler() -> None:
    from core.config import get_settings
    from core.services.triggers import SCHEDULER_POLL_SECONDS

    global _thread
    if not get_settings().trigger_scheduler_enabled:
        logger.info("Trigger scheduler disabled (TRIGGER_SCHEDULER_ENABLED=false)")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(SCHEDULER_POLL_SECONDS,), name="trigger-scheduler", daemon=True)
    _thread.start()
    logger.info("Trigger scheduler started (poll every %ss)", SCHEDULER_POLL_SECONDS)


def stop_trigger_scheduler() -> None:
    global _thread
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
    _thread = None


def fire_due_once() -> int:
    """给测试用：不等线程，手动跑一轮。"""
    return _tick()
