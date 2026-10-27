"""Native results report, preserving the approved live calculation and identities.

The request-local helpers and caches intentionally retain the original report's
scope. Do not lift mutable caches into module globals: users may run different
filters or update cutting templates between requests.
"""
import json

import frappe


def execute(filters=None):
    filters = filters or {}
    flt = frappe.utils.flt

    COMPONENT_EXCLUSION_CHECKBOX_GROUPS = (
        ("exception_component_1_input", "exception component 1"),
        ("exception_component_2_input", "exception component 2"),
    )


    def format_qty(value):
        if value in (None, ""):
            return ""
        try:
            number = float(value)
            if number.is_integer():
                return str(int(number))
            return ("%.3f" % number).rstrip("0").rstrip(".")
        except Exception:
            return str(value)


    def split_widths(total_width, leaf_count, split_type, fixed_leaf_width, to_float=frappe.utils.flt):
        lc = int(leaf_count or 1) or 1
        if lc <= 1:
            return [to_float(total_width)]

        fixed_width = to_float(fixed_leaf_width)
        total_width = to_float(total_width)

        if split_type == "ثابت" and fixed_width > 0 and lc > 1:
            remaining_width = total_width - fixed_width
            other_width = round(remaining_width / (lc - 1), 1)
            return [round(fixed_width, 1)] + [other_width] * (lc - 1)

        each_width = round(total_width / lc, 1)
        return [each_width] * lc


    def split_relative_widths(leaf_total_width, target_total_width, leaf_count, split_type, fixed_leaf_width, to_float=frappe.utils.flt):
        lc = int(leaf_count or 1) or 1
        leaf_total_width = to_float(leaf_total_width)
        if lc <= 1:
            leaf_parts = [leaf_total_width]
        else:
            fixed_width = to_float(fixed_leaf_width)
            if split_type == "ثابت" and fixed_width > 0:
                remaining_width = leaf_total_width - fixed_width
                other_width = round(remaining_width / (lc - 1), 1)
                leaf_parts = [round(fixed_width, 1)] + [other_width] * (lc - 1)
            else:
                each_width = round(leaf_total_width / lc, 1)
                leaf_parts = [each_width] * lc
        target_delta = to_float(target_total_width) - to_float(leaf_total_width)
        target_parts = []
        for leaf_part in leaf_parts:
            target_parts.append(round(to_float(leaf_part) + target_delta, 1))
        return target_parts


    def split_panel_widths(leaf_total_width, panel_total_width, leaf_count, split_type, fixed_leaf_width, to_float=frappe.utils.flt):
        lc = int(leaf_count or 1) or 1
        leaf_total_width = to_float(leaf_total_width)
        if lc <= 1:
            leaf_parts = [leaf_total_width]
        else:
            fixed_width = to_float(fixed_leaf_width)
            if split_type == "ثابت" and fixed_width > 0:
                remaining_width = leaf_total_width - fixed_width
                other_width = round(remaining_width / (lc - 1), 1)
                leaf_parts = [round(fixed_width, 1)] + [other_width] * (lc - 1)
            else:
                each_width = round(leaf_total_width / lc, 1)
                leaf_parts = [each_width] * lc
        panel_delta = to_float(panel_total_width) - to_float(leaf_total_width)
        panel_parts = []
        for leaf_part in leaf_parts:
            panel_parts.append(round(to_float(leaf_part) + panel_delta, 1))
        return panel_parts


    conditions = ["mr.docstatus < 2", "IFNULL(mri.item_code, '') != ''"]
    values = {}

    if frappe.get_meta("Material Request Item").has_field("custom_is_cancelled_row"):
        conditions.append("IFNULL(mri.custom_is_cancelled_row, 0) = 0")

    if filters.get("from_date"):
        conditions.append("mr.transaction_date >= %(from_date)s")
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions.append("mr.transaction_date <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    if filters.get("planned_date"):
        conditions.append("mr.custom_planned_date = %(planned_date)s")
        values["planned_date"] = filters["planned_date"]

    def normalize_multiselect_values(raw_value):
        values_list = raw_value
        if not values_list:
            return []

        if isinstance(values_list, str):
            normalized_text = values_list.strip()
            if normalized_text.startswith("[") and normalized_text.endswith("]"):
                normalized_text = normalized_text[1:-1]
            normalized_text = normalized_text.replace("\n", ",").replace(";", ",")
            normalized_text = normalized_text.replace('"', "").replace("'", "")
            values_list = []
            for value in normalized_text.split(","):
                cleaned_value = (value or "").strip()
                if cleaned_value:
                    values_list.append(cleaned_value)
        elif not isinstance(values_list, (list, tuple)):
            values_list = [values_list]

        cleaned_values = []
        for value in values_list:
            normalized_value = value
            if isinstance(normalized_value, dict):
                normalized_value = normalized_value.get("value") or normalized_value.get("name") or normalized_value.get("label") or normalized_value.get("description") or ""
            if isinstance(normalized_value, str):
                normalized_value = normalized_value.strip()
            if normalized_value and normalized_value not in cleaned_values:
                cleaned_values.append(normalized_value)

        return cleaned_values


    def split_component_exclusions(raw_value):
        components = []
        seen = {}
        text = raw_value or ""
        if isinstance(text, (list, tuple)):
            values = text
        else:
            values = str(text).replace("\n", ",").replace(";", ",").split(",")
        for value in values:
            component = (value or "").strip()
            if component and component not in seen:
                seen[component] = 1
                components.append(component)
        return components


    def split_component_exclusion_groups(raw_value):
        groups = []
        seen = {}
        text = raw_value or ""
        if isinstance(text, (list, tuple)):
            values = text
        else:
            normalized_text = str(text).strip()
            if normalized_text.startswith("[") and normalized_text.endswith("]"):
                normalized_text = normalized_text[1:-1].replace('"', "").replace("'", "")
            values = normalized_text.replace("\n", "|").replace(";", "|").replace(",", "|").split("|")
        for value in values:
            group_name = value
            if isinstance(group_name, dict):
                group_name = group_name.get("component_exclusion_group") or group_name.get("value") or group_name.get("name") or ""
            group_name = (group_name or "").strip()
            if group_name and group_name not in seen:
                seen[group_name] = 1
                groups.append(group_name)
        return groups


    def merge_component_exclusion_group_names(*values, splitter=split_component_exclusion_groups):
        groups = []
        seen = {}
        for value in values:
            if isinstance(value, str):
                parsed_values = splitter(value)
            else:
                parsed_values = value or []
            for group_name in parsed_values:
                group_name = (group_name or "").strip()
                if group_name and group_name not in seen:
                    seen[group_name] = 1
                    groups.append(group_name)
        return groups


    def get_row_component_exclusion_groups(
        row,
        merger=merge_component_exclusion_group_names,
        checkbox_groups=COMPONENT_EXCLUSION_CHECKBOX_GROUPS,
    ):
        groups = []
        for fieldname, group_name in checkbox_groups:
            if int(row.get(fieldname) or 0):
                groups = merger(groups, [group_name])
        return groups


    component_exclusion_group_cache = {}


    def merge_component_exclusions(*values):
        components = []
        seen = {}
        for value in values:
            for component in value or []:
                component = (component or "").strip()
                if component and component not in seen:
                    seen[component] = 1
                    components.append(component)
        return components


    def get_component_exclusion_group_components(group_name, cache=component_exclusion_group_cache, splitter=split_component_exclusions):
        group_name = (group_name or "").strip()
        if not group_name:
            return []
        if group_name not in cache:
            is_active = frappe.db.get_value("Store Component Exclusion Group", group_name, "is_active")
            if not int(is_active or 0):
                cache[group_name] = []
            else:
                rows = frappe.db.sql(
                    """
                    SELECT store_component
                    FROM `tabMaterial Request Item Excluded Store Component`
                    WHERE parenttype = 'Store Component Exclusion Group'
                      AND parentfield = 'components'
                      AND parent = %s
                    ORDER BY idx
                    """,
                    group_name,
                    as_dict=True,
                )
                cache[group_name] = splitter(
                    ",".join([(row.get("store_component") or "") for row in rows])
                )
        return cache[group_name]


    def get_component_exclusion_groups_components(
        group_names,
        merger=merge_component_exclusions,
        group_component_getter=get_component_exclusion_group_components,
    ):
        if isinstance(group_names, str):
            parsed_group_names = []
            seen_group_names = {}
            normalized_text = group_names.strip()
            if normalized_text.startswith("[") and normalized_text.endswith("]"):
                normalized_text = normalized_text[1:-1].replace('"', "").replace("'", "")
            for value in normalized_text.replace("\n", "|").replace(";", "|").replace(",", "|").split("|"):
                group_name = (value or "").strip()
                if group_name and group_name not in seen_group_names:
                    seen_group_names[group_name] = 1
                    parsed_group_names.append(group_name)
            group_names = parsed_group_names
        components = []
        for group_name in group_names or []:
            components = merger(
                components,
                group_component_getter(group_name),
            )
        return components

    workflow_states = normalize_multiselect_values(filters.get("workflow_state"))
    if workflow_states:
        escaped_states = []
        for state in workflow_states:
            escaped_states.append(frappe.db.escape(state))
        conditions.append("mr.workflow_state IN (" + ", ".join(escaped_states) + ")")

    workflow_styles = normalize_multiselect_values(filters.get("workflow_style"))

    material_requests = normalize_multiselect_values(filters.get("material_requests"))
    if material_requests:
        escaped_requests = []
        for request_name in material_requests:
            escaped_requests.append(frappe.db.escape(request_name))
        conditions.append("mri.parent IN (" + ", ".join(escaped_requests) + ")")
    elif filters.get("material_request"):
        conditions.append("mri.parent = %(material_request)s")
        values["material_request"] = filters["material_request"]

    customer_names = normalize_multiselect_values(filters.get("customer_names"))
    if customer_names:
        escaped_customers = []
        for customer_name in customer_names:
            escaped_customers.append(frappe.db.escape(customer_name))
        conditions.append("IFNULL(mr.customer_name, '') IN (" + ", ".join(escaped_customers) + ")")
    elif filters.get("customer_name"):
        conditions.append("IFNULL(mr.customer_name, '') LIKE %(customer_name)s")
        values["customer_name"] = "%" + filters["customer_name"] + "%"

    item_groups = normalize_multiselect_values(filters.get("item_groups"))
    if item_groups:
        escaped_item_groups = []
        for item_group in item_groups:
            escaped_item_groups.append(frappe.db.escape(item_group))
        conditions.append(
            "IFNULL((SELECT item_group FROM `tabItem` WHERE name = mri.item_code), '') IN ("
            + ", ".join(escaped_item_groups)
            + ")"
        )
    elif filters.get("item_group"):
        conditions.append("IFNULL((SELECT item_group FROM `tabItem` WHERE name = mri.item_code), '') = %(item_group)s")
        values["item_group"] = filters["item_group"]

    selected_components = normalize_multiselect_values(filters.get("component"))
    selected_colors = normalize_multiselect_values(filters.get("colors"))

    has_any_filter = any([
        filters.get("from_date"),
        filters.get("to_date"),
        filters.get("planned_date"),
        workflow_states,
        workflow_styles,
        selected_components,
        selected_colors,
        item_groups,
        filters.get("item_group"),
        material_requests,
        filters.get("material_request"),
        customer_names,
        filters.get("customer_name"),
    ])

    if not has_any_filter:
        conditions.append("1 = 0")

    where_clause = " AND ".join(conditions)

    query = """
        SELECT
            mri.parent AS طلب_المواد,
            mri.name AS row_name,
            mri.idx AS row_idx,
            mr.transaction_date AS التاريخ,
            mr.workflow_state AS الحالة,
            IFNULL((SELECT item_group FROM `tabItem` WHERE name = mri.item_code), '') AS item_group,
            mri.item_code AS كود_الصنف,
            mri.item_name AS اسم_الصنف,
            mri.qty AS الكمية,
            mri.custom_result_sliding_type AS sliding_type_input,
            mri.`عرض` AS width_input,
            mri.`طول` AS height_input,
            mri.`عرض_الجدار` AS wall_width_input,
            mri.custom_leaf_count AS leaf_count_input,
            mri.custom_split_type AS split_type_input,
            mri.custom_fixed_leaf_width AS fixed_leaf_width_input,
            mri.custom_net_leaf AS net_leaf_input,
            mri.custom_parquet AS parquet_input,
            mri.custom_taksiya_1 AS taksiya_1_input,
            mri.custom_taksiya_2 AS taksiya_2_input,
            mri.custom_exception_component_1 AS exception_component_1_input,
            mri.custom_exception_component_2 AS exception_component_2_input,
            mri.custom_frame_type AS frame_component_input,
            mri.custom_component_exclusion_groups AS component_exclusion_groups_input,
            mri.custom_result_leaf_w_text AS leaf_w_text_input,
            mri.custom_result_u_w_text AS u_w_text_input,
            mri.custom_result_panel_w_text AS panel_w_text_input
        FROM `tabMaterial Request Item` mri
        INNER JOIN `tabMaterial Request` mr ON mr.name = mri.parent
        WHERE """ + where_clause + """
        ORDER BY mr.transaction_date DESC, mri.parent, mri.idx
    """

    sql_rows = frappe.db.sql(query, values, as_dict=True)

    item_group_cache = {}
    cutting_cache = {}
    range_cache = {}
    component_labels = {}
    component_color_flags = {}
    component_group_map = {}

    for component_row in frappe.db.sql("SELECT component_name, label_ar, custom_has_color, custom_cutting_option_group FROM `tabStore Component`", as_dict=True):
        component_name = component_row.get("component_name") or ""
        if component_name:
            component_labels[component_name] = component_row.get("label_ar") or component_name
            component_color_flags[component_name] = int(component_row.get("custom_has_color") or 0)
            if (component_row.get("custom_cutting_option_group") or "").strip():
                component_group_map[component_name] = (component_row.get("custom_cutting_option_group") or "").strip()

    workflow_style_map = {}
    for workflow_state_row in frappe.db.sql("SELECT name, style FROM `tabWorkflow State`", as_dict=True):
        workflow_style_map[workflow_state_row.get("name") or ""] = workflow_state_row.get("style") or ""


    def get_item_group(item_code, cache=item_group_cache):
        if item_code not in cache:
            cache[item_code] = frappe.db.get_value("Item", item_code, "item_group") or ""
        return cache[item_code]


    def flag_value(val, default_val=1):
        if val is None or val == "":
            return int(default_val)
        try:
            return 1 if int(val) else 0
        except Exception:
            return int(default_val)


    def fallback_value(val):
        text = (val or "").strip().lower()
        if text in ["leaf", "none"]:
            return text
        return "none"


    def show_with_net_leaf_value(val):
        if val is None or val == "":
            return None
        text = str(val).strip()
        if text == "يظهر":
            return 1
        if text == "لا يظهر":
            return 0
        if text == "نعم":
            return 1
        if text == "لا":
            return 0
        try:
            return 1 if int(float(text)) else 0
        except Exception:
            return None


    def qty_calc_mode_value(val, per_leaf_flag=0):
        text = (val or "").strip()
        if text == "لكل درفة":
            text = "لكل جزء"
        if text == "بالمعادلة":
            text = "بالمعادلة على الإجمالي"
        if text in ["ثابت", "لكل جزء", "بالمعادلة على الإجمالي", "بالمعادلة لكل جزء"]:
            return text
        return "لكل جزء" if int(per_leaf_flag or 0) else "ثابت"


    def quantity_formula_value(val):
        return (val or "").strip()


    def uses_part_mode(mode):
        return mode in ["لكل جزء", "بالمعادلة لكل جزء"]


    def safe_formula_text(formula_text):
        text = (formula_text or "").strip().replace("w", "W").replace("h", "H")
        if not text:
            return ""
        allowed_chars = "0123456789WH+-*/(). "
        for char in text:
            if char not in allowed_chars:
                return ""
        return text


    def tokenize_formula(formula_text, formula_sanitizer=safe_formula_text, to_float=frappe.utils.flt):
        tokens = []
        text = formula_sanitizer(formula_text)
        if not text:
            return []
        index = 0
        prev_kind = "start"
        while index < len(text):
            char = text[index]
            if char == " ":
                index += 1
                continue
            if char in "WH":
                tokens.append(char)
                prev_kind = "value"
                index += 1
                continue
            if char.isdigit() or char == "." or (char == "-" and prev_kind in ["start", "operator", "("]):
                start = index
                if char == "-":
                    index += 1
                dot_count = 0
                has_digit = False
                while index < len(text) and (text[index].isdigit() or text[index] == "."):
                    if text[index] == ".":
                        dot_count += 1
                        if dot_count > 1:
                            return []
                    else:
                        has_digit = True
                    index += 1
                if not has_digit:
                    if text[start] == "-":
                        tokens.append(0.0)
                        tokens.append("-")
                        prev_kind = "operator"
                        continue
                    return []
                tokens.append(to_float(text[start:index]))
                prev_kind = "value"
                continue
            if char in "+-*/":
                if prev_kind not in ["value", ")"]:
                    return []
                tokens.append(char)
                prev_kind = "operator"
                index += 1
                continue
            if char == "(":
                tokens.append(char)
                prev_kind = "("
                index += 1
                continue
            if char == ")":
                tokens.append(char)
                prev_kind = ")"
                index += 1
                continue
            return []
        return tokens


    def evaluate_tokens(tokens, ref_w, ref_h, to_float=frappe.utils.flt):
        if not tokens:
            return 0
        precedence = {"+": 1, "-": 1, "*": 2, "/": 2}
        output = []
        operators = []
        for token in tokens:
            if token in ["W", "H"] or isinstance(token, float):
                output.append(token)
                continue
            if token in precedence:
                while operators and operators[-1] in precedence and precedence[operators[-1]] >= precedence[token]:
                    output.append(operators.pop())
                operators.append(token)
                continue
            if token == "(":
                operators.append(token)
                continue
            if token == ")":
                while operators and operators[-1] != "(":
                    output.append(operators.pop())
                if not operators or operators[-1] != "(":
                    return 0
                operators.pop()
        while operators:
            operator = operators.pop()
            if operator in ["(", ")"]:
                return 0
            output.append(operator)

        stack = []
        for token in output:
            if isinstance(token, float):
                stack.append(token)
                continue
            if token == "W":
                stack.append(to_float(ref_w))
                continue
            if token == "H":
                stack.append(to_float(ref_h))
                continue
            if len(stack) < 2:
                return 0
            b = to_float(stack.pop())
            a = to_float(stack.pop())
            if token == "+":
                stack.append(a + b)
            elif token == "-":
                stack.append(a - b)
            elif token == "*":
                stack.append(a * b)
            elif token == "/":
                if not b:
                    return 0
                stack.append(a / b)
        return to_float(stack[0]) if len(stack) == 1 else 0


    def evaluate_quantity_formula(formula_text, ref_w, ref_h, formula_tokenizer=tokenize_formula, token_evaluator=evaluate_tokens):
        tokens = formula_tokenizer(formula_text)
        return token_evaluator(tokens, ref_w, ref_h)


    def size_reference_value(val):
        text = (val or "").strip()
        if text in ["Leaf", "U", "Panel"]:
            return text
        return ""


    def resolve_reference_dims(reference, default_dims, leaf_dims, u_dims, panel_dims, to_float=frappe.utils.flt):
        if reference == "Leaf" and (to_float(leaf_dims[0]) or to_float(leaf_dims[1])):
            return leaf_dims
        if reference == "U" and (to_float(u_dims[0]) or to_float(u_dims[1])):
            return u_dims
        if reference == "Panel" and (to_float(panel_dims[0]) or to_float(panel_dims[1])):
            return panel_dims
        return default_dims


    def should_include_component_with_net_leaf(component_rows, behavior_resolver=show_with_net_leaf_value, mode_resolver=qty_calc_mode_value, part_mode_checker=uses_part_mode):
        has_explicit_setting = False
        has_show = False
        has_leaf_mode = False
        for current_row in component_rows:
            behavior = behavior_resolver(current_row.get("custom_net_leaf_behavior"))
            if behavior is not None:
                has_explicit_setting = True
                if behavior:
                    has_show = True
            if part_mode_checker(mode_resolver(current_row.get("custom_qty_calculation"), current_row.get("per_leaf"))):
                has_leaf_mode = True
        if has_explicit_setting:
            return has_show
        return has_leaf_mode


    def calculate_store_qty(range_row, default_dims, leaf_dims, u_dims, panel_dims, mode_resolver=qty_calc_mode_value, size_reference_resolver=size_reference_value, reference_resolver=resolve_reference_dims, formula_evaluator=evaluate_quantity_formula, to_float=frappe.utils.flt):
        mode = mode_resolver(range_row.get("custom_qty_calculation"), range_row.get("per_leaf"))
        base_qty = to_float(range_row.get("qty")) if range_row.get("qty") is not None else 1
        if mode not in ["بالمعادلة على الإجمالي", "بالمعادلة لكل جزء"]:
            return base_qty
        reference = size_reference_resolver(range_row.get("custom_size_reference"))
        ref_dims = reference_resolver(reference, default_dims, leaf_dims, u_dims, panel_dims)
        ref_w = ref_dims[0]
        ref_h = ref_dims[1]
        if not (to_float(ref_w) or to_float(ref_h)):
            return 0
        return round(formula_evaluator(range_row.get("custom_qty_formula"), ref_w, ref_h), 6)


    def get_cutting_rows(item_code, item_group, cache=cutting_cache):
        if item_code not in cache:
            rows = frappe.db.sql(
                """
                SELECT
                    cti.leaf_w, cti.leaf_h, cti.u_h, cti.u_w, cti.panel_w, cti.panel_h,
                    cti.notes, cti.sliding_type, cti.type, cti.parent, cti.item_code, cti.item_group,
                    cti.max_width, cti.max_height,
                    cti.default_leaf_count, cti.default_split_type, cti.fixed_leaf_width,
                    cti.parquet_deduction, cti.net_leaf_w_deduction, cti.net_leaf_h_deduction,
                    cti.net_leaf_panel_w, cti.net_leaf_panel_h,
                    cti.net_leaf_u_w, cti.net_leaf_u_h,
                    cti.custom_show_leaf_result, cti.custom_show_u_result, cti.custom_show_panel_result,
                    cti.custom_u_fallback, cti.custom_panel_fallback
                FROM `tabCutting Template Item` cti
                INNER JOIN `tabCutting Template` ct
                    ON ct.name = cti.parent AND IFNULL(ct.disabled, 0) = 0
                WHERE cti.item_code = %s
                """,
                item_code,
                as_dict=True,
            )

            if not rows and item_group:
                rows = frappe.db.sql(
                    """
                    SELECT
                        cti.leaf_w, cti.leaf_h, cti.u_h, cti.u_w, cti.panel_w, cti.panel_h,
                        cti.notes, cti.sliding_type, cti.type, cti.parent, cti.item_code, cti.item_group,
                        cti.max_width, cti.max_height,
                        cti.default_leaf_count, cti.default_split_type, cti.fixed_leaf_width,
                        cti.parquet_deduction, cti.net_leaf_w_deduction, cti.net_leaf_h_deduction,
                        cti.net_leaf_panel_w, cti.net_leaf_panel_h,
                        cti.net_leaf_u_w, cti.net_leaf_u_h,
                        cti.custom_show_leaf_result, cti.custom_show_u_result, cti.custom_show_panel_result,
                        cti.custom_u_fallback, cti.custom_panel_fallback
                    FROM `tabCutting Template Item` cti
                    INNER JOIN `tabCutting Template` ct
                        ON ct.name = cti.parent AND IFNULL(ct.disabled, 0) = 0
                    WHERE cti.item_group = %s
                    """,
                    item_group,
                    as_dict=True,
                )

            cache[item_code] = rows

        return cache[item_code]


    def get_best_cutting_row(rows, sliding_type):
        if not rows:
            return None

        best = None
        best_score = -1
        for row in rows:
            score = 0
            ok = True
            row_sliding_type = row.get("sliding_type") or ""
            if row_sliding_type:
                if row_sliding_type == sliding_type:
                    score += 1
                else:
                    ok = False

            if ok and score > best_score:
                best = row
                best_score = score

        return best or rows[0]


    def get_ranges(template_name, cache=range_cache):
        if template_name not in cache:
            cache[template_name] = frappe.db.sql(
                """
                SELECT
                    component, item_code, item_group, sliding_type,
                    width_from, width_to, height_from, height_to,
                    wall_width_from, wall_width_to, item, qty, per_leaf,
                    taksiya_1, taksiya_2, no_qitaat,
                    custom_net_leaf_behavior, custom_qty_calculation, custom_size_reference, custom_qty_formula,
                    custom_frame_type
                FROM `tabCutting Wall Range`
                WHERE parent = %s
                ORDER BY component, item_code
                """,
                template_name,
                as_dict=True,
            )
        return cache[template_name]


    def has_checkbox_range(ranges, component, checkbox_field):
        for range_row in ranges:
            if (range_row.get("component") or "") != component:
                continue
            if int(range_row.get(checkbox_field) or 0):
                return True
        return False


    def has_sliding_type_range(ranges, component, sliding_type, item_code="", item_group="", groups=component_group_map):
        sliding_type = (sliding_type or "").strip()
        if not sliding_type:
            return False
        group = (groups.get(component) or "").strip()
        for range_row in ranges:
            range_component = range_row.get("component") or ""
            if range_component != component:
                # Only explicit groups share option overrides; ungrouped components stay independent.
                if not group or (groups.get(range_component) or "").strip() != group:
                    continue
                # Keep sibling option overrides within the current item scope.
                range_item_code = range_row.get("item_code") or ""
                range_item_group = range_row.get("item_group") or ""
                if range_item_code:
                    if range_item_code != item_code:
                        continue
                elif range_item_group and range_item_group != item_group:
                    continue
            if (range_row.get("sliding_type") or "").strip() == sliding_type:
                return True
        return False


    def component_option_group(component, groups=component_group_map):
        return (groups.get((component or "").strip()) or "").strip()


    def is_same_component_option_group(source_component, target_component, groups=component_group_map):
        source_group = component_option_group(source_component, groups)
        return bool(source_group and source_group == component_option_group(target_component, groups))


    def has_frame_component_range(ranges, component, frame_component):
        frame_component = (frame_component or "").strip()
        if not frame_component:
            return False
        for range_row in ranges:
            range_component = range_row.get("component") or ""
            if range_component != component and not is_same_component_option_group(range_component, component):
                continue
            if (range_row.get("custom_frame_type") or "").strip() == frame_component:
                return True
        return False


    def find_range_match(ranges, component, item_code, item_group, sliding_type, default_dims, leaf_dims, u_dims, panel_dims, check_ww, taksiya_1=0, taksiya_2=0, no_qitaat=0, frame_component="", to_float=frappe.utils.flt, checkbox_checker=has_checkbox_range, sliding_range_checker=has_sliding_type_range, size_reference_resolver=size_reference_value, reference_resolver=resolve_reference_dims):
        best = None
        best_score = -1
        sliding_type = (sliding_type or "").strip()
        frame_component = (frame_component or "").strip()
        component_has_requested_sliding = sliding_range_checker(ranges, component, sliding_type, item_code, item_group)

        for range_row in ranges:
            if (range_row.get("component") or "") != component:
                continue

            ok = True
            score = 0
            rng_tak1 = int(range_row.get("taksiya_1") or 0)
            rng_tak2 = int(range_row.get("taksiya_2") or 0)
            rng_noq = int(range_row.get("no_qitaat") or 0)

            if rng_tak1 and not taksiya_1:
                ok = False
            if rng_tak2 and not taksiya_2:
                ok = False
            if rng_noq and not no_qitaat:
                ok = False

            if not rng_tak1 and taksiya_1 and checkbox_checker(ranges, component, "taksiya_1"):
                ok = False
            if not rng_tak2 and taksiya_2 and checkbox_checker(ranges, component, "taksiya_2"):
                ok = False
            if not rng_noq and no_qitaat and checkbox_checker(ranges, component, "no_qitaat"):
                ok = False

            range_frame_component = (range_row.get("custom_frame_type") or "").strip()
            if range_frame_component:
                if range_frame_component == frame_component:
                    score += 20
                else:
                    ok = False
            elif frame_component and has_frame_component_range(ranges, component, frame_component):
                ok = False

            if not ok:
                continue

            range_item_code = range_row.get("item_code") or ""
            range_item_group = range_row.get("item_group") or ""
            range_sliding_type = range_row.get("sliding_type") or ""

            if range_item_code:
                if range_item_code == item_code:
                    score += 10
                else:
                    ok = False
            elif range_item_group:
                if item_group and range_item_group == item_group:
                    score += 5
                else:
                    ok = False

            if component_has_requested_sliding:
                if range_sliding_type == sliding_type:
                    score += 100
                else:
                    ok = False
            elif range_sliding_type:
                if range_sliding_type == sliding_type:
                    score += 100
                else:
                    ok = False

            range_dims = reference_resolver(
                size_reference_resolver(range_row.get("custom_size_reference")),
                default_dims,
                leaf_dims,
                u_dims,
                panel_dims,
            )
            range_check_w = range_dims[0]
            range_check_h = range_dims[1]

            if ok and (to_float(range_row.get("width_from")) > 0 or to_float(range_row.get("width_to")) > 0):
                if not (to_float(range_row.get("width_from")) <= range_check_w <= to_float(range_row.get("width_to"))):
                    ok = False

            if ok and (to_float(range_row.get("height_from")) > 0 or to_float(range_row.get("height_to")) > 0):
                if not (to_float(range_row.get("height_from")) <= range_check_h <= to_float(range_row.get("height_to"))):
                    ok = False

            if ok and (to_float(range_row.get("wall_width_from")) > 0 or to_float(range_row.get("wall_width_to")) > 0):
                if not (to_float(range_row.get("wall_width_from")) <= check_ww <= to_float(range_row.get("wall_width_to"))):
                    ok = False

            if ok and score > best_score:
                best = range_row
                best_score = score

        return best


    component_group_order = [
        "بانل", "حلق", "برواز", "U", "فوم", "ربل", "مفصلات",
        "ترباس", "دبليو تي", "سيليكون", "مسمار", "كفر سحاب",
        "حديد سحاب", "إطار سحاب", "خشب سحاب",
    ]


    def component_sort_key(name, group_order=component_group_order):
        for index, prefix in enumerate(group_order):
            if name == prefix or name.startswith(prefix + " "):
                return index * 1000 + len(name)
        return 99000


    color_names = {
        "B": "أسود",
        "C": "بني",
        "CG": "بني محروق",
        "DG": "فيراني",
        "DGZ": "دارك جراي",
        "F": "فلاور",
        "G": "رصاصي",
        "H": "عسلي",
        "N": "بدون لون",
        "NS": "نيو سيلفر",
        "P": "خشبي",
        "S": "سيلفر",
        "T": "تك",
        "W": "أبيض",
        "WS": "قشر الجوز",
        "WT": "وايت تك",
        "Z": "بيج",
        "011-": "بني 011",
        "022-": "وايت تك 022",
    }


    def get_color_name(item_code, color_map=color_names):
        code = (item_code or "").strip()
        if not code:
            return "-"
        if "-" in code:
            color_code = (code.split("-") or [""])[0].strip()
            if color_code in ("011", "022"):
                color_code = color_code + "-"
        else:
            prefix_chars = []
            for char in code:
                if char.isdigit():
                    break
                prefix_chars.append(char)
            color_code = "".join(prefix_chars).strip()
        if color_code in color_map:
            return color_map[color_code]
        if not color_code:
            return "-"
        if len(color_code) <= 3:
            return color_code
        return "-"


    def component_uses_color(component_name, color_flags=component_color_flags):
        return 1 if int(color_flags.get(component_name) or 0) else 0


    def add_kdt_component_source(sources, source_key, source):
        # Keep one contribution per source/component/raw item. A part list names
        # the scope of this combined quantity; it never allocates qty to each part.
        if source_key not in sources:
            sources[source_key] = dict(source)
            sources[source_key]["part_nos"] = list(source.get("part_nos") or [])
            return
        current = sources[source_key]
        current["quantity"] = current["quantity"] + source["quantity"]
        if current.get("part_nos") and source.get("part_nos"):
            current["part_nos"] = sorted(set(current["part_nos"] + source["part_nos"]))
        else:
            # Empty scope means the contribution covers all parts of the row.
            current["part_nos"] = []


    aggregate_map = {}
    request_name_map = {}
    data_rows = []
    component_order = []
    available_color_names = dict.fromkeys(color_names.values())
    for selected_color in selected_colors:
        available_color_names[selected_color] = 1
    for color_source_row in sql_rows:
        color_source_code = (color_source_row.get("كود_الصنف") or "").strip()
        if color_source_code:
            available_color_names[get_color_name(color_source_code)] = 1

    for row in sql_rows:
        workflow_style = workflow_style_map.get((row.get("الحالة") or "").strip(), "") or ""
        workflow_style_key = workflow_style or "__blank__"
        if workflow_styles and workflow_style_key not in workflow_styles:
            continue

        item_code = (row.get("كود_الصنف") or "").strip()
        if not item_code:
            continue
        # Scope the source door before calculating any component. Components such
        # as hinges/foam have no own color, but still belong to this product color.
        if selected_colors and get_color_name(item_code) not in selected_colors:
            continue

        item_group = get_item_group(item_code)
        cutting_rows = get_cutting_rows(item_code, item_group)
        best = get_best_cutting_row(cutting_rows, (row.get("sliding_type_input") or "").strip())
        if not best:
            continue

        width_value = flt(row.get("width_input"))
        height_value = flt(row.get("height_input"))
        wall_width_value = flt(row.get("wall_width_input"))
        sliding_type = (row.get("sliding_type_input") or "").strip()
        leaf_count = int(row.get("leaf_count_input") or 0) or int(best.get("default_leaf_count") or 1) or 1
        split_type = (row.get("split_type_input") or "").strip() or (best.get("default_split_type") or "")
        fixed_leaf_width = flt(row.get("fixed_leaf_width_input")) or flt(best.get("fixed_leaf_width"))
        net_leaf = int(row.get("net_leaf_input") or 0)
        parquet = int(row.get("parquet_input") or 0)
        taksiya_1 = int(row.get("taksiya_1_input") or 0)
        taksiya_2 = int(row.get("taksiya_2_input") or 0)
        no_qitaat = int(row.get("exception_component_1_input") or 0)
        frame_component = (row.get("frame_component_input") or "").strip()
        item_qty = flt(row.get("الكمية")) or 1

        effective_height = height_value
        if parquet and flt(best.get("parquet_deduction")):
            effective_height = height_value + flt(best.get("parquet_deduction"))

        show_leaf = flag_value(best.get("custom_show_leaf_result"), 1)
        show_u = flag_value(best.get("custom_show_u_result"), 1)
        show_panel = flag_value(best.get("custom_show_panel_result"), 1)
        u_fallback = fallback_value(best.get("custom_u_fallback"))
        panel_fallback = fallback_value(best.get("custom_panel_fallback"))

        if net_leaf:
            leaf_w_total = width_value + flt(best.get("net_leaf_w_deduction"))
            leaf_h_total = effective_height + flt(best.get("net_leaf_h_deduction"))
            if show_u:
                u_w_total = width_value + flt(best.get("net_leaf_u_w")) if flt(best.get("net_leaf_u_w")) else (leaf_w_total if u_fallback == "leaf" else 0)
                u_h_total = effective_height + flt(best.get("net_leaf_u_h")) if flt(best.get("net_leaf_u_h")) else (leaf_h_total if u_fallback == "leaf" else 0)
            else:
                u_w_total = 0
                u_h_total = 0
            if show_panel:
                panel_w_total = width_value + flt(best.get("net_leaf_panel_w")) if flt(best.get("net_leaf_panel_w")) else (leaf_w_total if panel_fallback == "leaf" else 0)
                panel_h_total = effective_height + flt(best.get("net_leaf_panel_h")) if flt(best.get("net_leaf_panel_h")) else (leaf_h_total if panel_fallback == "leaf" else 0)
            else:
                panel_w_total = 0
                panel_h_total = 0
        else:
            leaf_w_total = width_value + flt(best.get("leaf_w"))
            leaf_h_total = effective_height + flt(best.get("leaf_h"))
            if show_u:
                u_w_total = width_value + flt(best.get("u_w")) if flt(best.get("u_w")) else (leaf_w_total if u_fallback == "leaf" else 0)
                u_h_total = effective_height + flt(best.get("u_h")) if flt(best.get("u_h")) else (leaf_h_total if u_fallback == "leaf" else 0)
            else:
                u_w_total = 0
                u_h_total = 0
            if show_panel:
                panel_w_total = width_value + flt(best.get("panel_w")) if flt(best.get("panel_w")) else (leaf_w_total if panel_fallback == "leaf" else 0)
                panel_h_total = effective_height + flt(best.get("panel_h")) if flt(best.get("panel_h")) else (leaf_h_total if panel_fallback == "leaf" else 0)
            else:
                panel_w_total = 0
                panel_h_total = 0

        all_ranges = get_ranges(best.get("parent"))
        component_names = []
        for range_row in all_ranges:
            component = range_row.get("component") or ""
            if component and component not in component_names:
                component_names.append(component)
        excluded_components = get_component_exclusion_groups_components(get_row_component_exclusion_groups(row))
        if excluded_components:
            filtered_component_names = []
            for component in component_names:
                if component not in excluded_components:
                    filtered_component_names.append(component)
            component_names = filtered_component_names

        stores = []
        component_rows_map = {}
        for component in component_names:
            component_rows_map[component] = []
            for range_row in all_ranges:
                if (range_row.get("component") or "") == component:
                    component_rows_map[component].append(range_row)

        if net_leaf:
            filtered_components = []
            for component in component_names:
                if should_include_component_with_net_leaf(component_rows_map.get(component, [])):
                    filtered_components.append(component)
            component_names = filtered_components

        for component in component_names:
            component_has_part_mode = False
            for range_row in component_rows_map.get(component, []):
                if uses_part_mode(qty_calc_mode_value(range_row.get("custom_qty_calculation"), range_row.get("per_leaf"))):
                    component_has_part_mode = True
                    break

            if leaf_count > 1 and component_has_part_mode:
                leaf_parts = split_widths(leaf_w_total, leaf_count, split_type, fixed_leaf_width)
                u_parts = split_relative_widths(leaf_w_total, u_w_total, leaf_count, split_type, fixed_leaf_width) if flt(u_w_total) else [0] * len(leaf_parts)
                panel_parts = split_panel_widths(leaf_w_total, panel_w_total, leaf_count, split_type, fixed_leaf_width) if flt(panel_w_total) else [0] * len(leaf_parts)
                part_groups = {}
                for part_index in range(len(leaf_parts)):
                    part_leaf_dims = (leaf_parts[part_index], leaf_h_total)
                    part_u_dims = (u_parts[part_index], u_h_total)
                    part_panel_dims = (panel_parts[part_index], panel_h_total)
                    part_default_dims = part_panel_dims
                    match = find_range_match(
                        all_ranges,
                        component,
                        item_code,
                        item_group,
                        sliding_type,
                        part_default_dims,
                        part_leaf_dims,
                        part_u_dims,
                        part_panel_dims,
                        wall_width_value,
                        taksiya_1,
                        taksiya_2,
                        no_qitaat,
                        frame_component,
                    )
                    if not match:
                        continue

                    item_key = match.get("item") or ""
                    add_qty = calculate_store_qty(match, part_default_dims, part_leaf_dims, part_u_dims, part_panel_dims)
                    if item_key not in part_groups:
                        part_groups[item_key] = {
                            "component": component,
                            "component_ar": component_labels.get(component, component),
                            "item": item_key,
                            "qty": add_qty,
                            "part_nos": [part_index + 1] if add_qty > 0 else [],
                        }
                    else:
                        part_groups[item_key]["qty"] = (part_groups[item_key].get("qty") or 0) + add_qty
                        if add_qty > 0:
                            part_groups[item_key]["part_nos"].append(part_index + 1)

                for part_group in part_groups.values():
                    stores.append(part_group)
            else:
                search_w = width_value
                search_h = effective_height
                default_dims = (search_w, search_h)
                leaf_dims = (leaf_w_total, leaf_h_total)
                u_dims = (u_w_total, u_h_total)
                panel_dims = (panel_w_total, panel_h_total)
                match = find_range_match(
                    all_ranges,
                    component,
                    item_code,
                    item_group,
                    sliding_type,
                    default_dims,
                    leaf_dims,
                    u_dims,
                    panel_dims,
                    wall_width_value,
                    taksiya_1,
                    taksiya_2,
                    no_qitaat,
                    frame_component,
                )
                if match:
                    stores.append({
                        "component": component,
                        "component_ar": component_labels.get(component, component),
                        "item": match.get("item") or "",
                        "qty": calculate_store_qty(match, default_dims, leaf_dims, u_dims, panel_dims),
                        "part_nos": [],
                    })

        store_summary_parts = []
        component_values = {}
        component_entries = {}
        for store in stores:
            component = (store.get("component") or "").strip()
            if selected_components and component not in selected_components:
                continue

            total_store_qty = flt(store.get("qty")) * item_qty
            if total_store_qty <= 0:
                continue

            if component and component not in component_order:
                component_order.append(component)

            item_text = (store.get("item") or "").strip()
            value_text = item_text + " x " + format_qty(total_store_qty) if item_text else format_qty(total_store_qty)
            label = (store.get("component_ar") or component or "مكون").strip()

            if component:
                aggregate_color = get_color_name(item_code) if component_uses_color(component) else ""
                aggregate_key = component + "||" + aggregate_color + "||" + item_text
                if aggregate_key not in aggregate_map:
                    aggregate_map[aggregate_key] = {
                        "component": component,
                        "label": label,
                        "item": item_text,
                        "color": aggregate_color,
                        "qty": 0,
                        "requests": {},
                        "row_count": 0,
                        "sources": {},
                    }
                aggregate_map[aggregate_key]["qty"] = (aggregate_map[aggregate_key].get("qty") or 0) + total_store_qty
                request_name = row.get("طلب_المواد") or ""
                if request_name:
                    aggregate_map[aggregate_key]["requests"][request_name] = 1
                aggregate_map[aggregate_key]["row_count"] = (aggregate_map[aggregate_key].get("row_count") or 0) + 1
                source_entry = {
                    "material_request": request_name,
                    "source_row": row.get("row_name") or "",
                    "component": component,
                    "raw_item": item_text,
                    "color": aggregate_color,
                    "quantity": total_store_qty,
                    "part_nos": sorted(store.get("part_nos") or []),
                    "part_count": leaf_count,
                }
                source_key = json.dumps(
                    [request_name, source_entry["source_row"], component, aggregate_color, item_text],
                    ensure_ascii=False, separators=(",", ":"))
                add_kdt_component_source(component_entries, source_key, source_entry)
                add_kdt_component_source(aggregate_map[aggregate_key]["sources"], source_key, source_entry)

            store_summary_parts.append(label + ": " + value_text)
            if component in component_values:
                component_values[component] = component_values[component] + " | " + value_text
            else:
                component_values[component] = value_text

        if selected_components and not component_values:
            continue

        stored_leaf_w_text = (row.get("leaf_w_text_input") or "").strip()
        stored_u_w_text = (row.get("u_w_text_input") or "").strip()
        stored_panel_w_text = (row.get("panel_w_text_input") or "").strip()

        if stored_leaf_w_text:
            leaf_w_display = stored_leaf_w_text
        elif not show_leaf:
            leaf_w_display = "-"
        elif leaf_w_total < 0:
            leaf_w_display = "-"
        elif leaf_count > 1:
            leaf_parts = []
            for part in split_widths(leaf_w_total, leaf_count, split_type, fixed_leaf_width):
                leaf_parts.append(format_qty(part))
            leaf_w_display = " + ".join(leaf_parts)
        else:
            leaf_w_display = format_qty(leaf_w_total)

        leaf_h_display = "-" if (not show_leaf or leaf_h_total < 0) else format_qty(leaf_h_total)
        if stored_u_w_text:
            u_w_display = stored_u_w_text
        else:
            u_w_display = "-" if (not show_u or u_w_total <= 0) else format_qty(u_w_total)
        u_h_display = "-" if (not show_u or u_h_total <= 0) else format_qty(u_h_total)

        if stored_panel_w_text:
            panel_w_display = stored_panel_w_text
        elif not show_panel or panel_w_total <= 0:
            panel_w_display = "-"
        elif leaf_count > 1:
            panel_parts = []
            for part in split_panel_widths(leaf_w_total, panel_w_total, leaf_count, split_type, fixed_leaf_width):
                panel_parts.append(format_qty(part))
            panel_w_display = " + ".join(panel_parts)
        else:
            panel_w_display = format_qty(panel_w_total)

        panel_h_display = "-" if (not show_panel or panel_h_total <= 0) else format_qty(panel_h_total)

        sorted_component_entries = []
        for component_entry_key in sorted(component_entries):
            sorted_component_entries.append(component_entries[component_entry_key])
        output_row = {
            "طلب_المواد": row.get("طلب_المواد"),
            "التاريخ": row.get("التاريخ"),
            "الحالة": row.get("الحالة"),
            "المكون": "",
            "_workflow_style": workflow_style_key,
            "كود_الصنف": item_code,
            "اسم_الصنف": row.get("اسم_الصنف"),
            "اللون": get_color_name(item_code),
            "الكمية": item_qty,
            "النموذج": best.get("parent") or "",
            "leaf_w": leaf_w_display,
            "leaf_h": leaf_h_display,
            "u_w": u_w_display,
            "u_h": u_h_display,
            "panel_w": panel_w_display,
            "panel_h": panel_h_display,
            "بيانات_المخازن": " | ".join(store_summary_parts) if store_summary_parts else "-",
            "_component_values": component_values,
            "_row_idx": row.get("row_idx") or 0,
            "_kdt_material_request_item": row.get("row_name") or "",
            "_kdt_component_names": "\n".join(component_values.keys()),
            "_kdt_component_entries": sorted_component_entries,
        }
        if output_row.get("طلب_المواد"):
            request_name_map[output_row.get("طلب_المواد")] = 1
        data_rows.append(output_row)

    component_order = sorted(component_order, key=component_sort_key)

    columns = [
        {"fieldname": "طلب_المواد", "label": "طلب المواد", "fieldtype": "Link", "options": "Material Request", "width": 120},
        {"fieldname": "التاريخ", "label": "التاريخ", "fieldtype": "Date", "width": 100},
        {"fieldname": "الحالة", "label": "الحالة", "fieldtype": "Data", "width": 120},
        {"fieldname": "المكون", "label": "المكون", "fieldtype": "Data", "width": 120},
        {"fieldname": "كود_الصنف", "label": "كود الصنف", "fieldtype": "Link", "options": "Item", "width": 110},
        {"fieldname": "اسم_الصنف", "label": "اسم الصنف", "fieldtype": "Data", "width": 170},
        {"fieldname": "اللون", "label": "اللون", "fieldtype": "Data", "width": 100},
        {"fieldname": "الكمية", "label": "الكمية", "fieldtype": "Float", "width": 70},
        {"fieldname": "النموذج", "label": "النموذج", "fieldtype": "Data", "width": 120},
        {"fieldname": "leaf_w", "label": "Leaf W", "fieldtype": "Data", "width": 90},
        {"fieldname": "leaf_h", "label": "Leaf H", "fieldtype": "Data", "width": 70},
        {"fieldname": "u_w", "label": "U W", "fieldtype": "Data", "width": 90},
        {"fieldname": "u_h", "label": "U H", "fieldtype": "Data", "width": 70},
        {"fieldname": "panel_w", "label": "Panel W", "fieldtype": "Data", "width": 90},
        {"fieldname": "panel_h", "label": "Panel H", "fieldtype": "Data", "width": 70},
        {"fieldname": "بيانات_المخازن", "label": "تفاصيل المخازن", "fieldtype": "Data", "width": 280},
    ]
    columns[0]["_kdt_available_colors"] = sorted(available_color_names)

    for component in component_order:
        fieldname = "store_" + (component or "").lower().replace(" ", "_").replace("-", "_").replace("/", "_")
        columns.append({
            "fieldname": fieldname,
            "label": component_labels.get(component, component),
            "fieldtype": "Data",
            "width": 140,
        })
        for output_row in data_rows:
            output_row[fieldname] = output_row.get("_component_values", {}).get(component, "-")

    for output_row in data_rows:
        output_row.pop("_component_values", None)

    data_rows = sorted(
        data_rows,
        key=lambda current_row: (
            current_row.get("التاريخ") or "",
            current_row.get("طلب_المواد") or "",
            current_row.get("_row_idx") or 0,
        ),
        reverse=True,
    )

    for output_row in data_rows:
        output_row.pop("_row_idx", None)

    total_store_qty_all = 0
    aggregate_rows = []
    for aggregate_row in aggregate_map.values():
        total_store_qty_all = total_store_qty_all + (aggregate_row.get("qty") or 0)
        aggregate_rows.append(aggregate_row)

    aggregate_rows = sorted(
        aggregate_rows,
        key=lambda current_row: (
            -1 * (current_row.get("qty") or 0),
            current_row.get("label") or "",
            current_row.get("item") or "",
        ),
    )

    summary_rows = []
    for aggregate_row in aggregate_rows:
        component_label = aggregate_row.get("label") or aggregate_row.get("component") or "-"
        item_text = aggregate_row.get("item") or "-"
        request_count = len(aggregate_row.get("requests") or [])
        line_count = aggregate_row.get("row_count") or 0
        states_text = ", ".join(sorted(aggregate_row.get("states") or []))
        models_text = ", ".join(sorted(aggregate_row.get("models") or []))
        sorted_component_sources = []
        for component_source_key in sorted(aggregate_row.get("sources") or {}):
            sorted_component_sources.append(aggregate_row["sources"][component_source_key])
        summary_rows.append({
            "طلب_المواد": "ملخص",
            "التاريخ": aggregate_row.get("last_date"),
            "الحالة": "ملخص المخازن",
            "المكون": component_label,
            "_component_label": component_label,
            "_kdt_component_name": aggregate_row.get("component") or "",
            "_kdt_component_key": json.dumps(
                [aggregate_row.get("component") or "", aggregate_row.get("color") or "", aggregate_row.get("item") or ""],
                ensure_ascii=False, separators=(",", ":")),
            "_kdt_sources": sorted_component_sources,
            "كود_الصنف": item_text,
            "اسم_الصنف": "عدد الطلبات: " + format_qty(request_count) + " | عدد السطور: " + format_qty(line_count),
            "اللون": aggregate_row.get("color") or "-",
            "الكمية": round(aggregate_row.get("qty") or 0, 3),
            "النموذج": models_text or "-",
            "leaf_w": "-",
            "leaf_h": "-",
            "u_w": "-",
            "u_h": "-",
            "panel_w": "-",
            "panel_h": "-",
            "بيانات_المخازن": states_text or "-",
        })

    if summary_rows:
        summary_rows.append({
            "طلب_المواد": "",
            "التاريخ": None,
            "الحالة": "",
            "المكون": "",
            "كود_الصنف": "",
            "اسم_الصنف": "",
            "اللون": "",
            "الكمية": "",
            "النموذج": "",
            "leaf_w": "",
            "leaf_h": "",
            "u_w": "",
            "u_h": "",
            "panel_w": "",
            "panel_h": "",
            "بيانات_المخازن": "",
        })
        data_rows = summary_rows + data_rows

    chart = None
    message = None
    report_summary = None

    if not has_any_filter:
        message = "اختر فلترًا واحدًا على الأقل لتشغيل التقرير."
        report_summary = [{"value": "بانتظار اختيار فلتر", "label": "حالة التقرير", "datatype": "Data"}]

    result = None
    data = (columns, data_rows, message, chart, report_summary, 0)

    return data
