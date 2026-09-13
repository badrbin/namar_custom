"""Narrow request-local acceleration of the native Link-field selector.

Permission decisions are never cached or replaced. Unsupported/custom native
paths use their ordinary implementation. The temporary instance method exists
only while the caller runs has_permission, after constructing its document.
"""
from __future__ import annotations

import ast
import builtins
from contextlib import contextmanager
import hashlib
import inspect
import operator
import textwrap
from types import CodeType, FunctionType, MethodType

_EQUAL, _NOT_EQUAL = operator.eq, operator.ne

# Bodies verified against Frappe v15.120.1. A framework change is a safe
# fallback, not an invitation to approximate a new permission implementation.
_NATIVE_BODIES = {
    "get_link_fields": "b071b3e132119dfa36a5ed809c676491a387dcbc7c8bbc7f99a105aa851f4bbd",
    "get_meta": "48973108864e33283613fcd8d3380a371690dce17ac1d954a4d875aee28cee7b",
    "get": "b29cde7eb5304860c10504fb85637e68cdbe88178d8b6293280173749f035608",
    "_filter": "4f5a2bb4737be65e0a1d1473ba3b234698d3a0b1fc63d7e4e1d5dbfa0aba1ec1",
    "compare": "113339153b3fe2dea449084530f3b178347325c547df8caeacd26f3854721bd6",
    "hget": "8e4d9589e1123c7c47f142a08dd2436a46c1bc8da0da233492a4bce90c6d5374",
}


def _code_shape(code):
    """Compare executable content, not source fragment line/filename offsets."""
    return (
        code.co_code, code.co_names, code.co_varnames, code.co_argcount,
        code.co_posonlyargcount, code.co_kwonlyargcount, code.co_freevars,
        code.co_cellvars, code.co_flags & ~0x1000000,  # annotations future flag
        code.co_stacksize, getattr(code, "co_exceptiontable", b""),
        tuple(_code_shape(value) if isinstance(value, CodeType) else value for value in code.co_consts),
    )


def _known_body(function, name):
    if not isinstance(function, FunctionType) or hasattr(function, "__wrapped__"):
        return False
    if function.__closure__ and (function.__code__.co_freevars != ("__class__",)
                                 or not isinstance(function.__closure__[0].cell_contents, type)):
        return False
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return False
    node = tree.body[0]
    body = ast.dump(node.args, include_attributes=False) + "\n" + ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
    if hashlib.sha256(body.encode()).hexdigest() != _NATIVE_BODIES[name]:
        return False
    defaults = tuple(ast.literal_eval(value) for value in node.args.defaults)
    actual = function.__defaults__ or ()
    if len(actual) != len(defaults) or any(type(a) is not type(b) or a != b for a, b in zip(actual, defaults)):
        return False
    keyword_defaults = {arg.arg: ast.literal_eval(value) for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults) if value is not None}
    if (function.__kwdefaults__ or {}) != keyword_defaults:
        return False
    # Source alone is not authority for an in-memory __code__ replacement.
    node.decorator_list = []
    if function.__closure__:
        owner = function.__closure__[0].cell_contents
        if vars(owner).get(name) is not function:
            return False
        wrapper = ast.ClassDef(name=owner.__name__, bases=[], keywords=[], body=[node], decorator_list=[], type_params=[])
        tree = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
        compiled = compile(tree, "<native-body-check>", "exec")
        compiled = next(value for value in compiled.co_consts if isinstance(value, CodeType))
    else:
        compiled = compile(ast.Module(body=[node], type_ignores=[]), "<native-body-check>", "exec")
    expected = next(value for value in compiled.co_consts if isinstance(value, CodeType))
    return _code_shape(function.__code__) == _code_shape(expected)


class NativeLinkFieldScope:
    def __init__(self, frappe):
        self.frappe = frappe
        self._context = None
        self._checked = False
        self._builtin_guards = ()

    def _prepare(self):
        if self._checked:
            return self._context
        self._checked = True
        try:
            from frappe.model import base_document, meta
            from frappe.utils import data
            from frappe.utils.redis_wrapper import RedisWrapper

            bindings = (
                (meta.Meta, "get_link_fields"), (meta, "get_meta"),
                (base_document.BaseDocument, "get"), (base_document, "_filter"),
                (data, "compare"), (RedisWrapper, "hget"),
            )
            guards = []
            for owner, name in bindings:
                function = getattr(owner, name)
                if not _known_body(function, name):
                    return None
                guards.append((owner, name, function, function.__code__, function.__defaults__,
                               function.__kwdefaults__, tuple(cell.cell_contents for cell in function.__closure__ or ())))
            builtin_guards = []
            for _, name, function, *_ in guards:
                if name in ("get_link_fields", "get", "_filter", "compare"):
                    for key in set(function.__code__.co_names) & vars(builtins).keys():
                        expected = getattr(builtins, key)
                        if function.__globals__.get(key, function.__builtins__.get(key)) is not expected:
                            return None
                        builtin_guards.append((function, key, expected))
            self._builtin_guards = tuple(builtin_guards)
            self._context = (meta.Meta, base_document, data, RedisWrapper, guards)
        except (ImportError, AttributeError, OSError, SyntaxError, TypeError, ValueError, StopIteration):
            self._context = None
        return self._context

    def _unchanged(self, context, *, selection_only=False):
        _, base, data, _, guards = context
        return (
            all(getattr(owner, name) is function and function.__code__ is code
                and function.__defaults__ is defaults and function.__kwdefaults__ is keyword_defaults
                and (not closure or tuple(cell.cell_contents for cell in function.__closure__ or ()) == closure)
                for owner, name, function, code, defaults, keyword_defaults, closure in guards
                if not selection_only or name not in ("get_meta", "hget"))
            and base.BaseDocument.get.__globals__.get("_filter") is base._filter
            and base._filter.__globals__.get("compare") is data.compare
            and data.compare.__globals__.get("operator_map") is data.operator_map
            and data.operator_map.get("=") is _EQUAL
            and data.operator_map.get("!=") is _NOT_EQUAL
            and all(function.__globals__.get(name, function.__builtins__.get(name)) is expected
                    for function, name, expected in self._builtin_guards)
        )

    @contextmanager
    def for_document(self, document):
        """Keep original Meta identity and restore even on permission errors."""
        installed = []
        try:
            context = self._prepare()
            if context is not None and self._unchanged(context):
                Meta, base, _, RedisWrapper, _ = context
                cache = self.frappe.cache
                # RedisWrapper.hget owns these unpickled objects in this HTTP
                # request. Do not replace its bucket, write Redis, or keep it.
                if type(cache) is RedisWrapper and "hget" not in vars(cache):
                    local_cache = self.frappe.local.cache
                    bucket = local_cache.get(cache.make_key("doctype_meta"))
                    primary = bucket.get(document.doctype) if isinstance(bucket, dict) else None
                    if type(primary) is Meta and document.meta is primary:
                        names = {document.doctype}
                        names.update(field.options for field in primary.get_table_fields())
                        hooks = self.frappe.get_hooks("has_permission") or {}
                        if not hooks.get("*") and not any(hooks.get(name) for name in names):
                            metas = [bucket.get(name) for name in names]
                            if all(type(item) is Meta and "get_link_fields" not in vars(item)
                                   and getattr(item.get, "__func__", None) is base.BaseDocument.get for item in metas):
                                for item in metas:
                                    def select(current, _context=context):
                                        if (getattr(current.get, "__func__", None) is not base.BaseDocument.get
                                                or not self._unchanged(_context, selection_only=True)):
                                            return type(current).get_link_fields(current)
                                        fields = current.__dict__.get("fields", [])
                                        if not fields:
                                            return []
                                        return [field for field in fields
                                                if getattr(field, "fieldtype", None) == "Link"
                                                and getattr(field, "options", None) != "[Select]"]

                                    method = MethodType(select, item)
                                    item.__dict__["get_link_fields"] = method
                                    installed.append((item, method))
        except (AttributeError, TypeError, KeyError):
            # Missing/custom context is not an approval failure. Undo a partial
            # installation before falling back to native permission checks.
            for item, method in installed:
                if item.__dict__.get("get_link_fields") is method:
                    del item.__dict__["get_link_fields"]
            installed.clear()
        try:
            yield
        finally:
            for item, method in installed:
                if item.__dict__.get("get_link_fields") is method:
                    del item.__dict__["get_link_fields"]
