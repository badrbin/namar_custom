
workflow = frappe.get_doc("Workflow", "طلب مواد")
user_roles = [r.role for r in frappe.get_all("Has Role", filters={"parent": frappe.session.user}, fields=["role"])]

transitions_map = {}
for t in workflow.transitions:
    if t.allowed in user_roles:
        state = t.state
        if state not in transitions_map:
            transitions_map[state] = []
        transitions_map[state].append({
            "action": t.action,
            "next": t.next_state
        })

frappe.response["message"] = transitions_map
