from __future__ import annotations
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PATH = Path(__file__).resolve().parents[1] / 'namar_custom/activity_permissions.py'


class ActivityPermissionTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType('frappe')
        self.frappe.whitelist = lambda **kwargs: lambda func: func
        self.frappe.get_doc = Mock()
        self.doc = self.frappe.get_doc.return_value
        self.activity = types.ModuleType('frappe.desk.form.activity')
        self.methods = ['get_activity_timeline', 'get_more_email_activities', 'get_more_milestone_activities']
        for name in self.methods:
            setattr(self.activity, name, Mock(return_value={'activities': [], 'kept_upstream_result': name}))
        form = types.ModuleType('frappe.desk.form')
        form.activity = self.activity
        self.modules = patch.dict(sys.modules, {'frappe': self.frappe, 'frappe.desk': types.ModuleType('frappe.desk'), 'frappe.desk.form': form, 'frappe.desk.form.activity': self.activity})
        self.modules.start()
        spec = importlib.util.spec_from_file_location('test_activity_permissions_target', PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def tearDown(self):
        self.modules.stop()

    def test_denied_reference_never_fetches_any_activity(self):
        self.doc.check_permission.side_effect = PermissionError('denied')
        for name in self.methods:
            with self.subTest(endpoint=name):
                kwargs = {'visible_types': ['log']} if name == self.methods[0] else {'start': 20}
                with self.assertRaises(PermissionError):
                    getattr(self.module, name)('User', 'Administrator', **kwargs)
                getattr(self.activity, name).assert_not_called()
        self.assertEqual(self.doc.check_permission.call_count, 3)
        self.doc.check_permission.assert_called_with('read')

    def test_allowed_timeline_preserves_visible_types_and_result(self):
        filters = ['comment', {'version': ['status']}]
        out = self.module.get_activity_timeline('Material Request', 'TEST-REF', filters)
        self.frappe.get_doc.assert_called_once_with('Material Request', 'TEST-REF')
        self.doc.check_permission.assert_called_once_with('read')
        self.activity.get_activity_timeline.assert_called_once_with('Material Request', 'TEST-REF', visible_types=filters)
        self.assertIs(out, self.activity.get_activity_timeline.return_value)

    def test_allowed_paging_preserves_start_and_result(self):
        for name in self.methods[1:]:
            with self.subTest(endpoint=name):
                out = getattr(self.module, name)('Sales Order', 'TEST-REF', start=40)
                getattr(self.activity, name).assert_called_once_with('Sales Order', 'TEST-REF', start=40)
                self.assertIs(out, getattr(self.activity, name).return_value)

    def test_missing_reference_does_not_reach_upstream(self):
        self.frappe.get_doc.side_effect = LookupError('missing')
        for name in self.methods:
            with self.subTest(endpoint=name):
                kwargs = {} if name == self.methods[0] else {'start': 0}
                with self.assertRaises(LookupError):
                    getattr(self.module, name)('Material Request', 'MISSING', **kwargs)
                getattr(self.activity, name).assert_not_called()

    def test_upstream_error_is_preserved_after_permission_check(self):
        self.activity.get_activity_timeline.side_effect = ValueError('invalid filter')
        with self.assertRaisesRegex(ValueError, 'invalid filter'):
            self.module.get_activity_timeline('Material Request', 'TEST-REF', 'invalid')
        self.doc.check_permission.assert_called_once_with('read')


if __name__ == '__main__':
    unittest.main(verbosity=2)
