from __future__ import annotations

import frappe
from frappe.utils import cint
from erpnext.stock.doctype.delivery_note.delivery_note import DeliveryNote


SCENARIO_RULE_DOCTYPE = "Material Request Scenario Rule"
POLICY_SCRIPT = "Validate Delivery Note Sales Order Policy"


class ScenarioDeliveryNote(DeliveryNote):
    """Apply the configured MR exception only to the sales-order requirement."""

    def so_required(self):
        policy_script = self._unlinked_delivery_policy()
        if policy_script is None:
            return super().so_required()

        # The existing policy validates the submitted reference order, customer,
        # company, unlinked item rows and remaining MR stock quantities. Running
        # it here also covers submission, where Before Save scripts do not run.
        # Exceptions deliberately propagate; a failed policy never grants bypass.
        policy_script.execute_doc(self)

    def _unlinked_delivery_policy(self):
        material_request = (self.get("custom_material_request") or "").strip()
        if not material_request:
            return None

        request = frappe.db.get_value(
            "Material Request",
            material_request,
            ["docstatus", "custom_request_scenario"],
            as_dict=True,
        )
        if not request or cint(request.get("docstatus")) != 1:
            return None

        scenario = (request.get("custom_request_scenario") or "").strip()
        if not scenario or not frappe.db.exists("DocType", SCENARIO_RULE_DOCTYPE):
            return None

        link_to_order = frappe.db.get_value(
            SCENARIO_RULE_DOCTYPE,
            {"scenario_name": scenario, "enabled": 1},
            "link_delivery_note_to_sales_order",
        )
        if link_to_order is None or cint(link_to_order) != 0:
            return None

        # Fail closed if the independently managed policy is unavailable.
        if not frappe.db.exists("Server Script", POLICY_SCRIPT):
            return None
        policy_script = frappe.get_doc("Server Script", POLICY_SCRIPT)
        if (
            cint(policy_script.get("disabled"))
            or policy_script.get("script_type") != "DocType Event"
            or policy_script.get("reference_doctype") != "Delivery Note"
            or not (policy_script.get("script") or "").strip()
        ):
            return None

        return policy_script
