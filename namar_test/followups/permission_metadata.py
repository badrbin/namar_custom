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
from pathlib import Path
import textwrap
from types import CodeType, FunctionType, MethodType

_EQUAL, _NOT_EQUAL = operator.eq, operator.ne

# Bodies verified against Frappe v15.120.1. A framework change is a safe
# fallback, not an invitation to approximate a new permission implementation.
_NATIVE_BODIES = {
    "get_link_fields": "b60215fa38c40e92de0c8f3f1dfebce3c3ed3baf24c3251e0007c2bf846b0c31",
    "get_meta": "5fe0cc1c4005347c1de42da5e69e4bc8f16e643c539add4d0662a93bf9ca672d",
    "get": "b9e867242708af8e126f8866c91d2dbb2beeb66d40d72945147550e0ca265857",
    "_filter": "29beb0f45f629423b5e34394b037e23bedd555677ab54d8c46c56449d3289337",
    "compare": "7c8fb975819d26a417308a50f97e26c7b18284a9d26f4996ae6ff4d2ed43ef4d",
    "hget": "e1943ed90a27846ba86ff1f359d6598bb9288151d77450184c486e7b7867d031",
}
_CODE_PATHS = {
    "get_link_fields": ("Meta", "get_link_fields"), "get_meta": ("get_meta",),
    "get": ("BaseDocument", "get"), "_filter": ("_filter",),
    "compare": ("compare",), "hget": ("RedisWrapper", "hget"),
}


def _native_ast_dump(node):
    # Python 3.13 omits empty fields by default; 3.11 always included them.
    # Keep identical source fingerprints without relaxing any body checks.
    try:
        return ast.dump(node, include_attributes=False, show_empty=True)
    except TypeError:
        return ast.dump(node, include_attributes=False)


def _code_shape(code):
    """Compare executable content, not source fragment line/filename offsets."""
    return (
        code.co_code, code.co_names, code.co_varnames, code.co_argcount,
        code.co_posonlyargcount, code.co_kwonlyargcount, code.co_freevars,
        code.co_cellvars, code.co_flags & ~0x1000000,  # annotations future flag
        code.co_stacksize, getattr(code, "co_exceptiontable", b""),
        tuple(_code_shape(value) if isinstance(value, CodeType) else value for value in code.co_consts),
    )


def _known_body(function, name, diagnostic=None, *, compiled_sources=None):
    def reject(reason):
        if diagnostic is not None:
            diagnostic["reject_step"] = reason
        return False

    if not isinstance(function, FunctionType) or hasattr(function, "__wrapped__"):
        return reject("function_type_or_wrapper")
    if function.__closure__ and (function.__code__.co_freevars != ("__class__",)
                                 or not isinstance(function.__closure__[0].cell_contents, type)):
        return reject("unsupported_closure")
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return reject("source_definition_shape")
    node = tree.body[0]
    body = _native_ast_dump(node.args) + "\n" + _native_ast_dump(ast.Module(body=node.body, type_ignores=[]))
    if hashlib.sha256(body.encode()).hexdigest() != _NATIVE_BODIES[name]:
        return reject("source_ast_hash")
    defaults = tuple(ast.literal_eval(value) for value in node.args.defaults)
    actual = function.__defaults__ or ()
    if len(actual) != len(defaults) or any(type(a) is not type(b) or a != b for a, b in zip(actual, defaults)):
        return reject("positional_defaults")
    keyword_defaults = {arg.arg: ast.literal_eval(value) for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults) if value is not None}
    if (function.__kwdefaults__ or {}) != keyword_defaults:
        return reject("keyword_defaults")
    # Preserve the module symbol table: CPython optimizes calls on imported
    # modules differently from calls in a standalone function AST fragment.
    # Compilation executes no imports or module statements. The caller owns
    # this code-only cache for the current request, never across requests.
    if function.__closure__:
        owner = function.__closure__[0].cell_contents
        if vars(owner).get(name) is not function:
            return reject("class_closure_owner")
    sources = compiled_sources if compiled_sources is not None else {}
    source_file = inspect.getsourcefile(function)
    if source_file not in sources:
        sources[source_file] = compile(Path(source_file).read_text(encoding="utf-8"), source_file, "exec", dont_inherit=True)
    expected = sources[source_file]
    for part in _CODE_PATHS[name]:
        candidates = [value for value in expected.co_consts if isinstance(value, CodeType) and value.co_name == part]
        if len(candidates) != 1:
            return reject("source_code_path")
        expected = candidates[0]
    current_shape, expected_shape = _code_shape(function.__code__), _code_shape(expected)
    matched = current_shape == expected_shape
    if diagnostic is not None:
        labels = ("bytecode", "names", "variable_names", "arg_count", "positional_only", "keyword_only",
                  "free_variables", "cell_variables", "flags", "stack_size", "exception_table", "constants")
        diagnostic["code_components"] = {
            label: {"matches": actual == wanted, **({
                "observed_hash": hashlib.sha256(repr(actual).encode()).hexdigest(),
                "expected_hash": hashlib.sha256(repr(wanted).encode()).hexdigest(),
            } if actual != wanted else {})}
            for label, actual, wanted in zip(labels, current_shape, expected_shape)}
        diagnostic["reject_step"] = "accepted" if matched else "compiled_code_shape"
    return matched


class NativeLinkFieldScope:
    def __init__(self, frappe):
        self.frappe = frappe
        self._context = None
        self._checked = False
        self._builtin_guards = ()
        self._compiled_sources = {}
        # Temporary TEST diagnostic; remove this opt-in instrumentation before
        # the final release. Never include users, documents or decisions.
        self._probe = None
        try:
            if (frappe.session.user == "Administrator"
                    and frappe.local.site == "testnamar.u.frappe.cloud"
                    and str(frappe.form_dict.get("namar_metadata_probe")) == "1"):
                self._probe = {"temporary": True, "any_scope_active": False}
                frappe.response["namar_metadata_probe"] = self._probe
        except AttributeError:
            pass

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
            if self._probe is not None:
                checks = {}
                for owner, name in bindings:
                    try:
                        function = getattr(owner, name)
                        node = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
                        source = _native_ast_dump(node.args) + "\n" + _native_ast_dump(ast.Module(body=node.body, type_ignores=[]))
                        details = {}
                        checks[name] = {"native_match": _known_body(function, name, details, compiled_sources=self._compiled_sources),
                                        "observed_hash": hashlib.sha256(source.encode()).hexdigest(),
                                        "approved_hash": _NATIVE_BODIES[name], "details": details}
                    except Exception as error:
                        checks[name] = {"native_match": False, "reason": type(error).__name__}
                self._probe["body_checks"] = checks
            guards = []
            for owner, name in bindings:
                function = getattr(owner, name)
                if not _known_body(function, name, compiled_sources=self._compiled_sources):
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
            if self._probe is not None:
                self._probe["reason"] = "unsupported_native_context"
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
        probe = None
        if self._probe is not None and "scope" not in self._probe:
            probe = self._probe["scope"] = {"reason": "native_context_unavailable"}
        try:
            context = self._prepare()
            if probe is not None:
                probe["context_ready"] = context is not None
                if context is not None:
                    probe["bindings_unchanged"] = self._unchanged(context)
                    probe["reason"] = "native_binding_guard" if not probe["bindings_unchanged"] else "cache_guard"
                    try:
                        _, base, data, _, guards = context
                        probe["bindings"] = {
                            name: {"identity": getattr(owner, name) is function, "code": function.__code__ is code,
                                   "defaults": function.__defaults__ is defaults, "keyword_defaults": function.__kwdefaults__ is keyword_defaults,
                                   "closure": not closure or tuple(cell.cell_contents for cell in function.__closure__ or ()) == closure}
                            for owner, name, function, code, defaults, keyword_defaults, closure in guards}
                        probe["aliases"] = {
                            "base_filter": base.BaseDocument.get.__globals__.get("_filter") is base._filter,
                            "filter_compare": base._filter.__globals__.get("compare") is data.compare,
                            "comparison_operators": data.compare.__globals__.get("operator_map") is data.operator_map,
                            "equals": data.operator_map.get("=") is _EQUAL, "not_equals": data.operator_map.get("!=") is _NOT_EQUAL}
                        probe["builtins"] = [{"name": name, "unchanged": function.__globals__.get(name, function.__builtins__.get(name)) is expected}
                                             for function, name, expected in self._builtin_guards]
                    except (AttributeError, TypeError, KeyError):
                        probe["binding_diagnostic_available"] = False
            if context is not None and self._unchanged(context):
                Meta, base, _, RedisWrapper, _ = context
                cache = self.frappe.cache
                if probe is not None:
                    probe["cache_class"] = type(cache).__name__
                    probe["cache_type_exact"] = type(cache) is RedisWrapper
                    probe["cache_instance_hget_override"] = "hget" in vars(cache)
                # RedisWrapper.hget owns these unpickled objects in this HTTP
                # request. Do not replace its bucket, write Redis, or keep it.
                if type(cache) is RedisWrapper and "hget" not in vars(cache):
                    local_cache = self.frappe.local.cache
                    bucket = local_cache.get(cache.make_key("doctype_meta"))
                    primary = bucket.get(document.doctype) if isinstance(bucket, dict) else None
                    if probe is not None:
                        probe.update({"bucket_present": isinstance(bucket, dict), "primary_present": primary is not None,
                                      "primary_type_exact": type(primary) is Meta, "primary_class": type(primary).__name__,
                                      "primary_identity_matches": document.meta is primary, "reason": "primary_meta_guard"})
                    if type(primary) is Meta and document.meta is primary:
                        names = {document.doctype}
                        names.update(field.options for field in primary.get_table_fields())
                        hooks = self.frappe.get_hooks("has_permission") or {}
                        if probe is not None:
                            probe.update({"wildcard_hook_present": bool(hooks.get("*")),
                                          "type_hook_present": any(bool(hooks.get(name)) for name in names), "reason": "permission_hook_guard"})
                        if not hooks.get("*") and not any(hooks.get(name) for name in names):
                            metas = [bucket.get(name) for name in names]
                            if probe is not None:
                                probe["metadata"] = [{"present": item is not None, "type_exact": type(item) is Meta,
                                                       "instance_selector_override": "get_link_fields" in vars(item) if hasattr(item, "__dict__") else False,
                                                       "get_is_native": getattr(getattr(item, "get", None), "__func__", None) is base.BaseDocument.get}
                                                      for item in metas]
                                probe["reason"] = "metadata_guard"
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
                                if probe is not None:
                                    probe["reason"] = "active"
                                if self._probe is not None:
                                    self._probe["any_scope_active"] = True
        except (AttributeError, TypeError, KeyError):
            if probe is not None:
                probe["reason"] = "unsupported_scope_context"
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
