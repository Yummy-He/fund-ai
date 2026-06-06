"""网络调用工具 — 超时保护 + 自动重试

中国金融数据 API（akshare 底层走东方财富/新浪等）网络波动频繁。
不加超时保护会导致整个流程无限期卡死，不会自己恢复。

用法:
    from src.utils.network import call_with_timeout
    result = call_with_timeout(ak.fund_fee_em, symbol="510050", indicator="赎回费率", timeout=30, retries=2)
"""

import concurrent.futures
import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger("fund_ai.utils.network")

T = TypeVar("T")


def call_with_timeout(
    func: Callable[..., T],
    *args,
    timeout: float = 30,
    retries: int = 2,
    **kwargs,
) -> T:
    """对可能无限期挂起的阻塞调用添加超时+重试保护

    在独立线程中执行 func，超时则抛 TimeoutError 并自动重试。
    非超时异常也会触发重试（兼顾网络瞬时故障）。

    Args:
        func: 要调用的函数
        *args: 位置参数
        timeout: 每次调用的超时时间（秒），默认 30
        retries: 超时/失败后的重试次数（总尝试次数 = retries + 1），默认 2
        **kwargs: 关键字参数

    Returns:
        函数返回值

    Raises:
        TimeoutError: 所有重试均超时
        原始异常: 非超时类错误且重试耗尽
    """
    last_error = None
    total_attempts = retries + 1
    func_name = getattr(func, "__name__", str(func))

    for attempt in range(total_attempts):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func, *args, **kwargs)
                return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            last_error = TimeoutError(
                f"调用超时 ({timeout}s, attempt {attempt+1}/{total_attempts}): {func_name}"
            )
            logger.warning(str(last_error))
        except Exception as e:
            last_error = e
            logger.warning(
                f"调用失败 (attempt {attempt+1}/{total_attempts}): {func_name} → {e}"
            )

        if attempt < total_attempts - 1:
            delay = min(2 ** attempt, 8)  # 1s → 2s → 4s → 最多 8s
            logger.info(f"等待 {delay}s 后重试 {func_name}...")
            time.sleep(delay)

    raise last_error  # type: ignore


def fetch_url_with_retry(
    url: str,
    timeout: float = 15,
    retries: int = 2,
    headers: dict | None = None,
) -> str:
    """带超时+重试的 HTTP GET 请求

    Args:
        url: 请求 URL
        timeout: 每次请求超时（秒）
        retries: 重试次数
        headers: 请求头

    Returns:
        响应文本

    Raises:
        requests.RequestException: 所有重试均失败
    """
    import requests

    last_error = None
    total_attempts = retries + 1

    for attempt in range(total_attempts):
        try:
            resp = requests.get(url, headers=headers or {}, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp.text
        except requests.Timeout as e:
            last_error = e
            logger.warning(f"HTTP 请求超时 ({timeout}s, attempt {attempt+1}/{total_attempts}): {url}")
        except requests.RequestException as e:
            last_error = e
            logger.warning(f"HTTP 请求失败 (attempt {attempt+1}/{total_attempts}): {url} → {e}")

        if attempt < total_attempts - 1:
            delay = min(2 ** attempt, 8)
            logger.info(f"等待 {delay}s 后重试...")
            time.sleep(delay)

    raise last_error  # type: ignore
