workflow = frappe.get_doc("Workflow", "طلب مواد")
user_roles = [
    row.role
    for row in frappe.get_all(
        "Has Role",
        filters={"parent": frappe.session.user},
        fields=["role"],
        limit_page_length=0,
    )
]

transitions_map = {}
for transition in workflow.transitions:
    if transition.allowed not in user_roles:
        continue
    state = transition.state
    if state not in transitions_map:
        transitions_map[state] = []
    transitions_map[state].append(
        {
            "action": transition.action,
            "next_state": transition.next_state,
        }
    )

frappe.response["message"] = transitions_map
