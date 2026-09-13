"""Worker-scoped fresh permission caches without flushing shared Redis.

Native permission helpers cache roles, grants and metadata in Redis. A worker
building a new authorization generation must not accept an older cached grant
or repopulate shared Redis from its transaction snapshot. This facade keeps only
those security namespaces in memory; locks, queues and unrelated cache keys
retain the native implementation. Use only in the single-threaded RQ worker.
"""

from __future__ import annotations

from contextlib import contextmanager


SECURITY_HASHES = frozenset({"roles", "user_permissions", "doctype_meta", "user_doc"})
SECURITY_VALUE_PREFIXES = ("document_cache::User::", "document_cache::System Settings::")
SECURITY_VALUES = frozenset({"active_domains"})
_MISSING = object()


def _security_value(key):
    return isinstance(key, str) and (key in SECURITY_VALUES or key.startswith(SECURITY_VALUE_PREFIXES))


class _FreshPermissionCache:
    def __init__(self, backend):
        self.backend = backend
        self.hashes = {}
        self.values = {}

    def __call__(self):
        return self

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def hget(self, name, key, generator=None, shared=False):
        if name not in SECURITY_HASHES:
            return self.backend.hget(name, key, generator=generator, shared=shared)
        if not key:
            return None
        values = self.hashes.setdefault(name, {})
        if key not in values and generator is not None:
            values[key] = generator()
        return values.get(key)

    def hset(self, name, key, value, shared=False, *args, **kwargs):
        if name not in SECURITY_HASHES:
            return self.backend.hset(name, key, value, shared, *args, **kwargs)
        if key is not None:
            self.hashes.setdefault(name, {})[key] = value

    def hdel(self, name, key, shared=False):
        if name not in SECURITY_HASHES:
            return self.backend.hdel(name, key, shared=shared)
        self.hashes.get(name, {}).pop(key, None)

    def hgetall(self, name):
        if name not in SECURITY_HASHES:
            return self.backend.hgetall(name)
        return dict(self.hashes.get(name, {}))

    def hexists(self, name, key, shared=False):
        if name not in SECURITY_HASHES:
            return self.backend.hexists(name, key, shared=shared)
        return key in self.hashes.get(name, {})

    def get_value(self, key, generator=None, user=None, expires=False, shared=False):
        if not _security_value(key):
            return self.backend.get_value(key, generator=generator, user=user, expires=expires, shared=shared)
        scoped = (key, user, shared)
        if scoped not in self.values and generator is not None:
            self.values[scoped] = generator()
        return self.values.get(scoped)

    def set_value(self, key, value, user=None, expires_in_sec=None, shared=False):
        if not _security_value(key):
            return self.backend.set_value(key, value, user=user, expires_in_sec=expires_in_sec, shared=shared)
        self.values[(key, user, shared)] = value


@contextmanager
def fresh_permission_cache(frappe):
    """Isolate native permission caches for one worker slice; restore on failure.

    Do not use in HTTP code: replacing a module cache reference is only safe in
    Frappe's isolated, synchronous background worker. This does not change the
    user, discard a shared cache, or bypass any native permission calculation.
    """
    if getattr(frappe, "request", None):
        raise RuntimeError("Fresh permission cache is restricted to background workers")
    original_cache = frappe.cache
    backend = original_cache if hasattr(original_cache, "hget") else original_cache()
    saved_local = {}
    for name, value in (
        ("role_permissions", {}), ("user_permissions", {}),
        ("meta_cache", {}), ("system_settings", None),
    ):
        saved_local[name] = getattr(frappe.local, name, _MISSING)
        setattr(frappe.local, name, value)
    old_values = getattr(frappe.db, "value_cache", _MISSING)
    if old_values is not _MISSING:
        frappe.db.value_cache = {}
    frappe.cache = _FreshPermissionCache(backend)
    try:
        yield
    finally:
        frappe.cache = original_cache
        for name, previous in saved_local.items():
            if previous is _MISSING:
                delattr(frappe.local, name)
            else:
                setattr(frappe.local, name, previous)
        if old_values is not _MISSING:
            frappe.db.value_cache = old_values
