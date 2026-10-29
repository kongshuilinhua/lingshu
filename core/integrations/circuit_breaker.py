"""
灵枢 Agent 平台 —— 模型调用三态熔断器。

🎯 架构角色：
    三态熔断器设计（CLOSED → OPEN → HALF_OPEN）。
    每个模型实例独立维护健康状态。当模型连续失败达到阈值时自动熔断，
    冷却期后进入半开状态放行探测请求，探测成功则恢复、失败则继续熔断。

    设计要点：
    - 每个模型独立熔断器实例（通过模型名作为 key）
    - CLOSED: 正常状态，记录连续失败次数
    - OPEN: 熔断状态，拒绝所有请求（快速失败，不发起实际 HTTP 调用）
    - HALF_OPEN: 冷却期后自动进入，放行 1 次探测请求
    - 探测成功 → CLOSED，探测失败 → OPEN（重新计时冷却期）
"""

import threading
import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "CLOSED"        # 正常——请求放行、记录失败
    OPEN = "OPEN"            # 熔断——拒绝请求、等待冷却
    HALF_OPEN = "HALF_OPEN"  # 半开——放行 1 次探测


class CircuitBreaker:
    """
    模型调用三态熔断器。

    🎯 状态转换：
        CLOSED ──(连续失败 >= threshold)──→ OPEN
        OPEN   ──(冷却 timewindow 秒后)──→ HALF_OPEN
        HALF_OPEN ──(探测成功)──→ CLOSED
        HALF_OPEN ──(探测失败)──→ OPEN

    🧠 默认参数：
        - failure_threshold=3: 连续失败 3 次触发熔断
        - timewindow=60: 冷却 60 秒后进入半开状态
    """

    def __init__(self, failure_threshold: int = 3, timewindow: int = 60):
        self.failure_threshold = failure_threshold
        self.timewindow = timewindow
        self._failures = 0
        self._last_failure_time = 0.0
        self._state = CircuitState.CLOSED
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """线程安全地读取当前状态（OPEN 状态在冷却期满后自动过渡到 HALF_OPEN）。"""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._last_failure_time >= self.timewindow:
                    self._state = CircuitState.HALF_OPEN
                    self._failures = 0
            return self._state

    def allow_request(self) -> bool:
        """当前是否允许发起请求。CLOSED 或 HALF_OPEN 状态返回 True。"""
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self):
        """记录一次成功调用——重置为 CLOSED 状态。"""
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED

    def record_failure(self):
        """记录一次失败调用——连续失败达到阈值时切换为 OPEN。"""
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN

    def status(self) -> dict:
        """
        导出当前状态信息，供健康检查端点使用。

        ⚠️ 状态必须走 `state` property 读：OPEN 冷却期满后它会自动过渡到 HALF_OPEN，
            直接读 `_state` 会让健康检查永远显示 OPEN，看起来像一直没恢复。
        """
        # 先取状态（property 内部会拿锁并做过渡），再单独拿计数，避免嵌套加锁
        state = self.state
        with self._lock:
            return {
                "state": state.value,
                "failures": self._failures,
                "threshold": self.failure_threshold,
                "timewindow_s": self.timewindow,
            }


class RedisCircuitBreaker:
    """
    三态熔断器的 Redis 共享态变体。

    🎯 与 CircuitBreaker 接口完全一致（allow_request / record_success / record_failure / status），
        可直接替换。区别在于失败计数与开断状态存 Redis，多 worker / 多实例共享同一判定——
        进程内版本在多 worker 下各算各的（一个 worker 熔断了、别的还在打），这里消除了该不一致。

    🛡️ Redis 不可用时底层 store 各操作自动降级（allow_request 放行、记录类无副作用），
        等效退化为「不熔断」，不阻断主流程。
    """

    def __init__(self, name: str, failure_threshold: int = 3, timewindow: int = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timewindow = timewindow

    def allow_request(self) -> bool:
        from core.services.rag_cache import redis_store

        return redis_store.breaker_allow(self.name)

    def record_success(self):
        from core.services.rag_cache import redis_store

        redis_store.breaker_on_success(self.name)

    def record_failure(self):
        from core.services.rag_cache import redis_store

        redis_store.breaker_on_failure(self.name, self.failure_threshold, self.timewindow)

    def status(self) -> dict:
        from core.services.rag_cache import redis_store

        return {
            "name": self.name,
            "open": not redis_store.breaker_allow(self.name),
            "threshold": self.failure_threshold,
            "timewindow_s": self.timewindow,
            "backend": "redis",
        }
