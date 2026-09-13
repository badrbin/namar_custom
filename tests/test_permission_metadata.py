from __future__ import annotations

import ast
from contextlib import contextmanager
import os
import hashlib
from pathlib import Path
from types import SimpleNamespace, MethodType, ModuleType, CodeType, FunctionType
import operator
import random
import sys
import unittest
from unittest.mock import patch

from namar_test.followups.permission_metadata import NativeLinkFieldScope, _known_body, _native_ast_dump


class Base:
    def get(self, key, filters=None):
        rows = self.__dict__.get(key)
        if not filters:
            return rows
        return [row for row in rows or () if getattr(row, "fieldtype", None) == "Link"
                and getattr(row, "options", None) != "[Select]"]


class Meta(Base):
    def __init__(self, fields=(), tables=()):
        self.fields = list(fields)
        self.tables = list(tables)

    def get_link_fields(self):
        return self.get("fields", {"fieldtype": "Link", "options": ["!=", "[Select]"]})

    def get_table_fields(self):
        return [SimpleNamespace(options=value) for value in self.tables]


class Cache:
    def make_key(self, name):
        return b"test|" + name.encode()


class Scope(NativeLinkFieldScope):
    """Scope lifecycle tests; native body acceptance is tested separately."""
    valid = True

    def _prepare(self):
        return (Meta, SimpleNamespace(BaseDocument=Base), None, Cache, [])

    def _unchanged(self, context, **kwargs):
        return self.valid


def fixture():
    parent = Meta([SimpleNamespace(fieldtype="Link", options="Company")], ["Child"])
    child = Meta([SimpleNamespace(fieldtype="Data", options=""), SimpleNamespace(fieldtype="Link", options="Warehouse")])
    bucket = {"Parent": parent, "Child": child}
    cache = Cache()
    frappe = SimpleNamespace(cache=cache, local=SimpleNamespace(cache={cache.make_key("doctype_meta"): bucket}),
                             get_hooks=lambda name: {})
    return frappe, SimpleNamespace(doctype="Parent", meta=parent), parent, child, bucket


class PermissionMetadataScopeTest(unittest.TestCase):
    def test_native_ast_fingerprint_keeps_empty_fields_on_all_python_versions(self):
        node = ast.parse("f()").body[0].value
        self.assertEqual(_native_ast_dump(node), "Call(func=Name(id='f', ctx=Load()), args=[], keywords=[])")
        original = ast.dump

        def python_311_dump(node, *, include_attributes=False, **kwargs):
            if "show_empty" in kwargs:
                raise TypeError("unexpected keyword argument 'show_empty'")
            try:
                return original(node, include_attributes=include_attributes, show_empty=True)
            except TypeError:
                return original(node, include_attributes=include_attributes)

        with patch("namar_test.followups.permission_metadata.ast.dump", python_311_dump):
            self.assertEqual(_native_ast_dump(node), "Call(func=Name(id='f', ctx=Load()), args=[], keywords=[])")

    def test_original_identity_fresh_lists_and_native_permission_fields(self):
        f, doc, parent, child, bucket = fixture()
        with Scope(f).for_document(doc):
            self.assertIs(doc.meta, bucket["Parent"])
            self.assertIn("get_link_fields", vars(parent))
            first, second = child.get_link_fields(), child.get_link_fields()
            self.assertIsNot(first, second)
            self.assertEqual(first, [child.fields[1]])
            self.assertIs(first[0], child.fields[1])
            first[0].ignore_user_permissions = 1
            self.assertEqual(child.get_link_fields()[0].ignore_user_permissions, 1)
        self.assertNotIn("get_link_fields", vars(parent))
        self.assertNotIn("get_link_fields", vars(child))

    def test_field_mutations_replacement_and_empty_values_remain_current(self):
        f, doc, parent, child, _ = fixture()
        with Scope(f).for_document(doc):
            child.fields[0].fieldtype = "Link"
            self.assertEqual(len(child.get_link_fields()), 2)
            child.fields[1].options = "[Select]"
            self.assertEqual(child.get_link_fields(), [child.fields[0]])
            parent.fields = [SimpleNamespace(fieldtype="Link", options=None)]
            self.assertEqual(parent.get_link_fields(), parent.fields)
            for empty in ([], (), None, False):
                parent.fields = empty
                self.assertEqual(parent.get_link_fields(), [])
            del parent.fields
            self.assertEqual(parent.get_link_fields(), [])
        self.assertFalse(hasattr(parent, "fields"))

    def test_field_properties_keep_predicate_order_and_selection(self):
        f, doc, parent, _, _ = fixture()
        calls = []
        class Field:
            @property
            def fieldtype(self):
                calls.append("fieldtype")
                return "Data"
            @property
            def options(self):
                calls.append("options")
                raise AssertionError("options must short-circuit")
        parent.fields = [Field()]
        with Scope(f).for_document(doc):
            self.assertEqual(parent.get_link_fields(), [])
        self.assertEqual(calls, ["fieldtype"])

    def test_exception_restores_original_and_keeps_unrelated_metadata_changes(self):
        f, doc, parent, child, _ = fixture()
        with self.assertRaisesRegex(RuntimeError, "permission failed"):
            with Scope(f).for_document(doc):
                parent.some_lazy_cache = [1]
                raise RuntimeError("permission failed")
        self.assertEqual(parent.some_lazy_cache, [1])
        self.assertNotIn("get_link_fields", vars(parent))
        self.assertNotIn("get_link_fields", vars(child))

    def test_nested_scope_does_not_remove_outer_override(self):
        f, doc, parent, _, _ = fixture()
        with Scope(f).for_document(doc):
            outer = vars(parent)["get_link_fields"]
            with Scope(f).for_document(doc):
                self.assertIs(vars(parent)["get_link_fields"], outer)
            self.assertIs(vars(parent)["get_link_fields"], outer)
        self.assertNotIn("get_link_fields", vars(parent))

    def test_cache_replacement_and_invalidation_are_never_reversed(self):
        for invalidate in (False, True):
            f, doc, parent, _, bucket = fixture()
            replacement = Meta()
            with Scope(f).for_document(doc):
                if invalidate:
                    f.local.cache.clear()
                else:
                    bucket["Parent"] = replacement
            self.assertNotIn("get_link_fields", vars(parent))
            self.assertEqual(f.local.cache, {} if invalidate else {f.cache.make_key("doctype_meta"): bucket})
            if not invalidate:
                self.assertIs(bucket["Parent"], replacement)

    def test_does_not_clobber_later_instance_override(self):
        f, doc, parent, _, _ = fixture()
        later = MethodType(lambda self: ["new native implementation"], parent)
        with Scope(f).for_document(doc):
            parent.get_link_fields = later
        self.assertIs(parent.get_link_fields, later)

    def test_instance_get_changed_during_scope_is_respected(self):
        f, doc, parent, _, _ = fixture()
        with Scope(f).for_document(doc):
            parent.get = lambda *args: []
            self.assertEqual(parent.get_link_fields(), [])

    def test_custom_hooks_meta_instance_get_and_unowned_cache_fall_back(self):
        for scenario in ("parent_hook", "child_hook", "wildcard_hook", "override", "custom_get", "subclass", "unowned"):
            f, doc, parent, child, bucket = fixture()
            if scenario.endswith("hook"):
                key = {"parent_hook": "Parent", "child_hook": "Child", "wildcard_hook": "*"}[scenario]
                f.get_hooks = lambda name: {key: ["custom.permission"]}
            elif scenario == "override":
                child.get_link_fields = lambda: []
            elif scenario == "custom_get":
                child.get = lambda *args: []
            elif scenario == "subclass":
                class CustomMeta(Meta): pass
                bucket["Child"] = CustomMeta()
            else:
                bucket["Parent"] = Meta()
            with self.subTest(scenario=scenario), Scope(f).for_document(doc):
                self.assertNotIn("get_link_fields", vars(parent))

    def test_guard_change_during_scope_reverts_to_current_native_selector(self):
        f, doc, parent, _, _ = fixture()
        scope = Scope(f)
        with scope.for_document(doc):
            scope.valid = False
            with patch.object(Meta, "get_link_fields", lambda self: ["native changed"]):
                self.assertEqual(parent.get_link_fields(), ["native changed"])

    def test_unknown_runtime_never_changes_metadata(self):
        f, doc, parent, _, _ = fixture()
        with NativeLinkFieldScope(f).for_document(doc):
            self.assertNotIn("get_link_fields", vars(parent))


SOURCE = Path(os.environ.get("FRAPPE_TEST_SOURCE_ROOT", "/nonexistent"))


@unittest.skipUnless((SOURCE / "model/meta.py").is_file(), "Provide native Frappe source for body guard validation")
class NativeBodyGuardTest(unittest.TestCase):
    def load_full_source(self, file, name, cls=None):
        path = SOURCE / file
        source = path.read_text()
        compiled = compile(source, str(path), "exec")
        nodes = ast.parse(source).body
        if cls:
            compiled = next(value for value in compiled.co_consts if isinstance(value, CodeType) and value.co_name == cls)
            nodes = next(node for node in nodes if isinstance(node, ast.ClassDef) and node.name == cls).body
        code = next(value for value in compiled.co_consts if isinstance(value, CodeType) and value.co_name == name)
        node = next(node for node in nodes if isinstance(node, ast.FunctionDef) and node.name == name)
        defaults = tuple(ast.literal_eval(value) for value in node.args.defaults) or None
        owner = type(cls, (), {}) if cls else None
        closure = ((lambda: owner).__closure__[0],) if code.co_freevars == ("__class__",) else None
        function = FunctionType(code, {"__file__": str(path)}, name, defaults, closure)
        if owner is not None:
            setattr(owner, name, function)
        return function

    def test_real_full_source_compilation_is_accepted_not_only_extracted_ast(self):
        for file, cls, name in (
            ("model/meta.py", "Meta", "get_link_fields"), ("model/meta.py", None, "get_meta"),
            ("model/base_document.py", "BaseDocument", "get"), ("model/base_document.py", None, "_filter"),
            ("utils/data.py", None, "compare"), ("utils/redis_wrapper.py", "RedisWrapper", "hget"),
        ):
            with self.subTest(name=name):
                self.assertTrue(_known_body(self.load_full_source(file, name, cls), name))

    @unittest.skipUnless(sys.version_info[:2] == (3, 11), "Observed live fingerprints belong to Python 3.11")
    def test_full_source_matches_both_live_python311_bytecode_fingerprints(self):
        for file, cls, name, expected in (
            ("utils/data.py", None, "compare", "08dda47611c8cab1b884b6dcb86e9777d163912d869393a2565737139ad7ebfe"),
            ("utils/redis_wrapper.py", "RedisWrapper", "hget", "0561f9c7996e8dea56aafb34824a020a410a0ff22c8e8e03bab28dc85a2f3a91"),
        ):
            function = self.load_full_source(file, name, cls)
            self.assertEqual(hashlib.sha256(repr(function.__code__.co_code).encode()).hexdigest(), expected)
            self.assertTrue(_known_body(function, name))

    def test_source_compilation_is_request_owned_and_never_executes_module(self):
        sources = {}
        first = self.load_full_source("model/meta.py", "get_meta")
        second = self.load_full_source("model/meta.py", "get_link_fields", "Meta")
        with patch("builtins.exec", side_effect=AssertionError("native source must not execute")):
            self.assertTrue(_known_body(first, "get_meta", compiled_sources=sources))
            self.assertEqual(len(sources), 1)
            compiled = next(iter(sources.values()))
            self.assertTrue(_known_body(second, "get_link_fields", compiled_sources=sources))
            self.assertEqual(len(sources), 1)
            self.assertIs(next(iter(sources.values())), compiled)

    def load(self, file, name, cls=None):
        return self.load_full_source(file, name, cls)

    def test_accepts_native_functions_and_super_closure(self):
        for file, cls, name in (
            ("model/meta.py", "Meta", "get_link_fields"), ("model/meta.py", None, "get_meta"),
            ("model/base_document.py", "BaseDocument", "get"), ("model/base_document.py", None, "_filter"),
            ("utils/data.py", None, "compare"), ("utils/redis_wrapper.py", "RedisWrapper", "hget"),
        ):
            with self.subTest(name=name):
                self.assertTrue(_known_body(self.load(file, name, cls), name))

    def test_in_memory_code_change_does_not_trust_unchanged_disk_source(self):
        function = self.load("model/meta.py", "get_link_fields", "Meta")
        function.__code__ = (lambda self: []).__code__
        self.assertFalse(_known_body(function, "get_link_fields"))

    def test_defaults_changed_before_activation_are_not_accepted(self):
        function = self.load("model/base_document.py", "get", "BaseDocument")
        self.assertTrue(_known_body(function, "get"))
        function.__defaults__ = (None, 1, None)
        self.assertFalse(_known_body(function, "get"))
        function = self.load("utils/data.py", "compare")
        function.__defaults__ = ("Int",)
        self.assertFalse(_known_body(function, "compare"))

    def test_changed_native_exception_table_is_not_accepted(self):
        function = self.load("utils/redis_wrapper.py", "hget", "RedisWrapper")
        if not hasattr(function.__code__, "co_exceptiontable"):
            self.skipTest("This Python has no code exception table")
        self.assertTrue(function.__code__.co_exceptiontable)
        function.__code__ = function.__code__.replace(co_exceptiontable=b"")
        self.assertFalse(_known_body(function, "hget"))

    @contextmanager
    def native_scope(self):
        """Actual source functions, with an in-memory cache instead of Redis."""
        base = ModuleType("frappe.model.base_document")
        data = ModuleType("frappe.utils.data")
        meta_module = ModuleType("frappe.model.meta")
        redis_module = ModuleType("frappe.utils.redis_wrapper")
        get = self.load("model/base_document.py", "get", "BaseDocument")
        base.BaseDocument = type("BaseDocument", (), {"get": get})
        base._filter = self.load("model/base_document.py", "_filter")
        data.compare = self.load("utils/data.py", "compare")
        data.operator_map = {"=": operator.eq, "!=": operator.ne}
        data.compare.__globals__["operator_map"] = data.operator_map
        base._filter.__globals__["compare"] = data.compare
        get.__globals__["_filter"] = base._filter
        link = self.load("model/meta.py", "get_link_fields", "Meta")
        meta_module.Meta = type("Meta", (base.BaseDocument,), {
            "get_link_fields": link, "get_table_fields": lambda self: [],
        })
        meta_module.get_meta = self.load("model/meta.py", "get_meta")
        hget = self.load("utils/redis_wrapper.py", "hget", "RedisWrapper")
        redis_module.RedisWrapper = hget.__closure__[0].cell_contents
        redis_module.RedisWrapper.make_key = lambda self, name: b"test|" + name.encode()
        cache = redis_module.RedisWrapper()
        primary = meta_module.Meta()
        primary.fields = [SimpleNamespace(fieldtype="Link", options="Company"), SimpleNamespace(fieldtype="Data")]
        f = SimpleNamespace(cache=cache, local=SimpleNamespace(cache={cache.make_key("doctype_meta"): {"Parent": primary}}),
                            get_hooks=lambda name: {})
        model = ModuleType("frappe.model")
        model.base_document, model.meta = base, meta_module
        utils = ModuleType("frappe.utils")
        utils.data, utils.redis_wrapper = data, redis_module
        with patch.dict(sys.modules, {
            "frappe.model": model, "frappe.model.base_document": base, "frappe.model.meta": meta_module,
            "frappe.utils": utils, "frappe.utils.data": data, "frappe.utils.redis_wrapper": redis_module,
        }):
            yield NativeLinkFieldScope(f), SimpleNamespace(doctype="Parent", meta=primary), base, data

    def test_full_guard_activates_on_native_functions_and_preserves_live_operator_changes(self):
        with self.native_scope() as (scope, doc, base, data):
            original = doc.meta.get_link_fields()
            with scope.for_document(doc):
                self.assertIn("get_link_fields", vars(doc.meta))
                self.assertEqual(doc.meta.get_link_fields(), original)
                data.operator_map["="] = lambda a, b: False
                self.assertEqual(doc.meta.get_link_fields(), [])
            self.assertNotIn("get_link_fields", vars(doc.meta))

    def test_custom_comparison_before_scope_falls_back_without_installing(self):
        with self.native_scope() as (scope, doc, base, data):
            data.operator_map["="] = lambda a, b: False
            with scope.for_document(doc):
                self.assertNotIn("get_link_fields", vars(doc.meta))
                self.assertEqual(doc.meta.get_link_fields(), [])

    def test_shadowed_native_builtin_is_not_bypassed(self):
        with self.native_scope() as (scope, doc, base, data):
            with scope.for_document(doc):
                self.assertIn("get_link_fields", vars(doc.meta))
                base._filter.__globals__["getattr"] = lambda *args: None
                self.assertEqual(doc.meta.get_link_fields(), [])
            self.assertNotIn("get_link_fields", vars(doc.meta))

    def test_seeded_native_parity_with_replaced_fields_and_missing_attributes(self):
        rng = random.Random(61722)
        with self.native_scope() as (scope, doc, base, data):
            for _ in range(200):
                fields = []
                for index in range(rng.randrange(120)):
                    values = {"fieldname": str(index), "ignore_user_permissions": rng.choice((0, 1))}
                    if rng.randrange(5):
                        values["fieldtype"] = rng.choice(("Link", "Data", "Dynamic Link", None, ""))
                    if rng.randrange(5):
                        values["options"] = rng.choice(("Company", "Warehouse", "[Select]", "", None))
                    fields.append(SimpleNamespace(**values))
                doc.meta.fields = fields
                expected = doc.meta.get_link_fields()
                with scope.for_document(doc):
                    self.assertIn("get_link_fields", vars(doc.meta))
                    self.assertEqual(doc.meta.get_link_fields(), expected)
                    self.assertIsNot(doc.meta.get_link_fields(), doc.meta.get_link_fields())


if __name__ == "__main__":
    unittest.main()
