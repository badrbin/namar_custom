"""Run actual Frappe validators/CSV serializers against the boundary, without DB.

Set FRAPPE_SOURCE_PATHS to os.pathsep-separated directories containing frappe's
__init__.py (e.g. pinned v15.116.1/frappe and v15.119.0/frappe). Pydantic v2 is
required for the real typing validator. No installed bench, HTTP or SQL is used.
Absent source checkouts, normal unit discovery reports an explicit skip.
"""
import ast
from contextlib import nullcontext
import csv
import importlib.util
import inspect
from io import StringIO
import json
import os
from pathlib import Path
import sys
from types import MethodType, ModuleType, SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from test_ai_readonly_boundary import Row, make_boundary
from namar_test.ai_readonly.print_resources import render_pdf


def source_definitions(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError(f'External Frappe definitions changed: {path}')
    for node in nodes:
        node.decorator_list = []
    # Do not inherit a test module's future annotations: the real runtime
    # validator deliberately skips string annotations, masking the regression.
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec', dont_inherit=True), namespace)
    return namespace


def external_access_log(source, record):
    exceptions = ModuleType('frappe.exceptions')
    source_definitions(source/'exceptions.py', ['FrappeTypeError'], vars(exceptions))
    with patch.dict(sys.modules, {'frappe.exceptions': exceptions}):
        spec = importlib.util.spec_from_file_location('frappe_external_typing_validator', source/'utils/typing_validations.py')
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
    namespace = source_definitions(source/'core/doctype/access_log/access_log.py', ['make_access_log'],
                                   {'Any': Any, '_make_access_log': lambda *args: record.append(args)})
    return validator.validate_argument_types(namespace['make_access_log']), exceptions.FrappeTypeError, namespace['make_access_log']


def external_exporter(source, frappe):
    namespace = {'csv': csv, 'StringIO': StringIO,
                 'FORMULA_TRIGGER_CHARS': ('=', '+', '-', '@', '\t', '\r')}
    csv_path = source/'utils/csvutils.py'
    names = ['UnicodeWriter']
    if any(isinstance(node, ast.FunctionDef) and node.name == 'escape_formula_injection'
           for node in ast.parse(csv_path.read_text()).body):
        names.append('escape_formula_injection')
    source_definitions(csv_path, names, namespace)
    namespace.update({'frappe': frappe, '_': lambda value, **kwargs: value,
                      'parse_json': lambda value: json.loads(value) if isinstance(value, str) else value,
                      'cint': lambda value: int(value or 0), 'cstr': lambda value: str(value or '')})
    source_definitions(source/'core/doctype/data_export/exporter.py', ['get_data_keys', 'DataExporter'], namespace)
    return namespace['DataExporter']


class FrameworkContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = [Path(value) for value in os.environ.get('FRAPPE_SOURCE_PATHS', '').split(os.pathsep) if value]
        if not cls.sources:
            raise unittest.SkipTest('Set FRAPPE_SOURCE_PATHS to pinned real framework sources')
        import pydantic
        if not hasattr(pydantic, 'TypeAdapter'):
            raise unittest.SkipTest('The real Frappe validator requires Pydantic v2')
        for source in cls.sources:
            if not (source/'__init__.py').is_file():
                raise AssertionError(f'Invalid Frappe source directory: {source}')

    def test_real_access_log_validator_rejects_dict_when_columns_is_annotated(self):
        for source in self.sources:
            with self.subTest(source=str(source)):
                record = []
                call, error, original = external_access_log(source, record)
                if 'columns' in original.__annotations__:
                    self.assertNotIsInstance(original.__annotations__['columns'], str)
                    with self.assertRaisesRegex(error, "Argument 'columns'"):
                        call(doctype='Note', columns={'Note': ['name']})
                    self.assertEqual(record, [])
                call(doctype='Note', columns=json.dumps({'Note': ['name']}))
                self.assertEqual(json.loads(record[-1][-1]), {'Note': ['name']})

    def test_real_begin_commit_and_rollback_preserve_the_read_only_transaction(self):
        for source in self.sources:
            with self.subTest(source=str(source)):
                boundary, frappe, _, _ = make_boundary()
                path = source/'database/database.py'
                tree = ast.parse(path.read_text())
                database = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Database')
                methods = [node for node in database.body if isinstance(node, ast.FunctionDef) and node.name in ('begin', 'commit', 'rollback')]
                namespace = {'frappe': frappe}
                exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), 'exec', dont_inherit=True), namespace)
                statements = []
                callbacks = SimpleNamespace(reset=lambda: None, run=lambda: None)
                connection = SimpleNamespace(sql=statements.append, value_cache={}, before_commit=callbacks,
                                             after_commit=callbacks, before_rollback=callbacks, after_rollback=callbacks)
                for name in ('begin', 'commit', 'rollback'):
                    setattr(connection, name, MethodType(namespace[name], connection))
                frappe.local.db = connection; frappe.db = connection
                boundary._start_read_only()
                connection.commit(); connection.rollback()
                self.assertEqual(statements, ['rollback', 'START TRANSACTION READ ONLY',
                    'START TRANSACTION READ ONLY', 'commit', 'START TRANSACTION READ ONLY',
                    'rollback', 'START TRANSACTION READ ONLY'])
                self.assertTrue(frappe.flags.read_only)
                self.assertFalse(frappe.conf.read_from_replica)

    def test_boundary_uses_real_validated_access_log_and_real_csv_exporter(self):
        for source in self.sources:
            with self.subTest(source=str(source)):
                boundary, frappe, meta, calls = make_boundary()
                record = []
                access_log, _, _ = external_access_log(source, record)
                frappe._dict = source_definitions(source/'types/frappedict.py', ['_dict'], {})['_dict']
                for index, field in enumerate(meta.fields):
                    field.update(idx=index+1, label=field.fieldname, reqd=0, parent='Customer', hidden=0)
                frappe.db.get_table_columns_description = lambda table: [Row(name='name')] + [Row(name=f.fieldname) for f in meta.fields]
                doc = Row(doctype='Customer', name='C-1', owner=frappe.session.user, meta=meta)
                doc.check_permission = lambda action: None
                doc.apply_fieldlevel_read_permissions = lambda: None
                doc.as_dict = lambda: {'doctype': 'Customer', 'name': 'C-1', 'owner': frappe.session.user,
                                       'customer_name': 'Visible Name', 'secret_amount': 999, 'api_secret': 'hidden'}
                frappe.get_doc = lambda *args: doc
                exporter = ModuleType('frappe.core.doctype.data_export.exporter')
                exporter.DataExporter = external_exporter(source, frappe)
                access = ModuleType('frappe.core.doctype.access_log.access_log')
                access.make_access_log = access_log
                permissions = ModuleType('frappe.permissions')
                permissions.can_export = lambda *args, **kwargs: True
                args = {'doctype': 'Customer', 'all_doctypes': False, 'file_type': 'CSV',
                        'select_columns': {'Customer': ['name', 'customer_name']}}
                plan = {'args': args, 'role': 'الذكاء الاصطناعي', 'user': frappe.session.user, 'policy': {}}
                with patch.dict(sys.modules, {'frappe.core.doctype.data_export.exporter': exporter,
                     'frappe.core.doctype.access_log.access_log': access, 'frappe.permissions': permissions}):
                    boundary._export(plan)
                self.assertIsInstance(record[0][-1], str)
                self.assertEqual(json.loads(record[0][-1]), args['select_columns'])
                self.assertEqual(frappe.response['type'], 'csv')
                rows = list(csv.reader(StringIO(frappe.response['result'])))
                self.assertEqual(rows[-1], ['', 'C-1', 'Visible Name'])
                self.assertNotIn('999', frappe.response['result'])
                self.assertNotIn('hidden', frappe.response['result'])

    def test_real_get_print_signature_accepts_scoped_document_arguments(self):
        for source in self.sources:
            with self.subTest(source=str(source)):
                fn = next(node for node in ast.parse((source/'utils/print_utils.py').read_text()).body
                          if isinstance(node, ast.FunctionDef) and node.name == 'get_print')
                # Bind to the actual externally defined signature without
                # invoking website routing or a PDF renderer.
                fn.body = [ast.Pass()]; fn.decorator_list = []
                namespace = {'Literal': __import__('typing').Literal}
                exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                             str(source/'utils/print_utils.py'), 'exec', dont_inherit=True), namespace)
                signature = inspect.signature(namespace['get_print'])
                _, frappe, _, _ = make_boundary()
                frappe.local.response = Row()
                bound_calls = []
                def get_print(*args, **kwargs):
                    bound_calls.append(signature.bind(*args, **kwargs).arguments)
                    return '<p>Reviewed print</p>'
                frappe.get_print = get_print
                utils = ModuleType('frappe.utils'); utils.get_url = lambda **kwargs: 'https://test.example'
                pdf = ModuleType('frappe.utils.pdf'); pdf.get_pdf = lambda value: b'%PDF-test'
                fmt = ModuleType('frappe.utils.print_format')
                fmt.print_language = lambda language: nullcontext()
                fmt.validate_print_permission = lambda doc: None
                document = Row(doctype='Note', name='N-1')
                modules = {'frappe': frappe, 'frappe.utils': utils, 'frappe.utils.pdf': pdf,
                           'frappe.utils.print_format': fmt}
                with patch.dict(sys.modules, modules), patch('namar_test.ai_readonly.print_resources.inline_images', side_effect=lambda content, *args: content):
                    for hidden in (0, 1):
                        render_pdf(document, 'Standard', {'letterhead': 'Selected Letter Head', 'no_letterhead': hidden}, {})
                        self.assertIs(bound_calls[-1]['doc'], document)
                        self.assertEqual(bound_calls[-1]['letterhead'], 'Selected Letter Head')
                        self.assertEqual(bound_calls[-1]['no_letterhead'], hidden)
                        self.assertFalse(bound_calls[-1]['as_pdf'])


if __name__ == '__main__': unittest.main()
