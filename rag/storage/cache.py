import json
import logging
import os
from typing import Any, Optional

import redis

from rag.utils.runtime_errors import sanitize_text


logger = logging.getLogger(__name__)

# 这个类实现了一个基于 Redis 的缓存系统，提供了获取、设置和删除缓存数据的功能。它使用 JSON 格式来存储数据，并且支持设置缓存的过期时间（TTL）。
# 通过环境变量可以配置 Redis 的连接 URL、键前缀和默认 TTL。
class RedisCache:
    """Redis 缓存封装类，给业务代码提供简单的 get/set/delete 接口。"""
    def __init__(self):
        """初始化对象状态，保存后续方法会复用的配置、连接或依赖实例。"""
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.key_prefix = os.getenv("REDIS_KEY_PREFIX", "supermew")
        self.default_ttl = int(os.getenv("REDIS_CACHE_TTL_SECONDS", "300"))
        self._client = None

    def _get_client(self):
        """Redis 缓存封装：获取 client，屏蔽配置、缓存或外部依赖细节。"""
        if self._client is None:
            self._client = redis.Redis.from_url(self.redis_url, decode_responses=True)
        return self._client

    def _key(self, key: str) -> str:
        """Redis 缓存封装：封装 key 相关逻辑，供上层流程复用。"""
        return f"{self.key_prefix}:{key}"

    def get_json(self, key: str) -> Optional[Any]:
        """Redis 缓存封装：获取 json，屏蔽配置、缓存或外部依赖细节。"""
        try:
            value = self._get_client().get(self._key(key))
            if not value:
                return None
            return json.loads(value)
        except Exception as exc:
            logger.warning("Redis get_json failed for key %s: %s", key, sanitize_text(exc))
            return None

    def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Redis 缓存封装：设置 json 状态，让后续调用共享该上下文。"""
        try:
            payload = json.dumps(value, ensure_ascii=False)
            self._get_client().setex(self._key(key), ttl or self.default_ttl, payload)
        except Exception as exc:
            logger.warning("Redis set_json failed for key %s: %s", key, sanitize_text(exc))
            return

    def delete(self, key: str) -> None:
        """Redis 缓存封装：封装 delete 相关逻辑，供上层流程复用。"""
        try:
            self._get_client().delete(self._key(key))
        except Exception as exc:
            logger.warning("Redis delete failed for key %s: %s", key, sanitize_text(exc))
            return

    def delete_pattern(self, pattern: str) -> None:
        """Redis 缓存封装：删除 pattern 数据，用于重建或清理流程。"""
        try:
            full_pattern = self._key(pattern)
            keys = list(self._get_client().scan_iter(match=full_pattern))
            if keys:
                self._get_client().delete(*keys)
        except Exception as exc:
            logger.warning("Redis delete_pattern failed for pattern %s: %s", pattern, sanitize_text(exc))
            return


cache = RedisCache()
