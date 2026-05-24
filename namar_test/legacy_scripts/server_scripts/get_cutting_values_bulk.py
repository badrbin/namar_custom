flt = frappe.utils.flt
item_codes_str = frappe.form_dict.get('item_codes', '')
sliding_types_str = frappe.form_dict.get('sliding_types', '')
widths_str = frappe.form_dict.get('widths', '')
heights_str = frappe.form_dict.get('heights', '')
wall_widths_str = frappe.form_dict.get('wall_widths', '')
leaf_counts_str = frappe.form_dict.get('leaf_counts', '')
split_types_str = frappe.form_dict.get('split_types', '')
fixed_leaf_widths_str = frappe.form_dict.get('fixed_leaf_widths', '')
taksiya1_str = frappe.form_dict.get('taksiya1s', '')
taksiya2_str = frappe.form_dict.get('taksiya2s', '')
no_qitaat_str = frappe.form_dict.get('no_qitaats', '')
net_leafs_str = frappe.form_dict.get('net_leafs', '')
parquets_str = frappe.form_dict.get('parquets', '')
square_counts_str = frappe.form_dict.get('square_counts', '')
glass_types_str = frappe.form_dict.get('glass_types', '')
glass_models_str = frappe.form_dict.get('glass_models', '')
component_exclusion_groups_str = frappe.form_dict.get('component_exclusion_groups', '')
component_exclusions_str = frappe.form_dict.get('component_exclusions', '')
frame_components_str = frappe.form_dict.get('frame_components', '')

if not item_codes_str:
	frappe.response['message'] = {'values': {}}
else:
	codes = item_codes_str.split(',')
	slides = sliding_types_str.split(',') if sliding_types_str else []
	widths = widths_str.split(',') if widths_str else []
	heights = heights_str.split(',') if heights_str else []
	walls = wall_widths_str.split(',') if wall_widths_str else []
	while len(slides) < len(codes):
		slides.append('')
	while len(widths) < len(codes):
		widths.append('0')
	while len(heights) < len(codes):
		heights.append('0')
	while len(walls) < len(codes):
		walls.append('0')
	leaf_counts = leaf_counts_str.split(',') if leaf_counts_str else []
	split_types = split_types_str.split(',') if split_types_str else []
	fixed_lws = fixed_leaf_widths_str.split(',') if fixed_leaf_widths_str else []
	while len(leaf_counts) < len(codes):
		leaf_counts.append('1')
	while len(split_types) < len(codes):
		split_types.append('')
	while len(fixed_lws) < len(codes):
		fixed_lws.append('0')
	taksiya1s = taksiya1_str.split(',') if taksiya1_str else []
	taksiya2s = taksiya2_str.split(',') if taksiya2_str else []
	no_qitaats = no_qitaat_str.split(',') if no_qitaat_str else []
	net_leafs = net_leafs_str.split(',') if net_leafs_str else []
	parquets = parquets_str.split(',') if parquets_str else []
	square_counts = square_counts_str.split(',') if square_counts_str else []
	glass_types = glass_types_str.split(',') if glass_types_str else []
	glass_models = glass_models_str.split(',') if glass_models_str else []
	component_exclusion_groups = component_exclusion_groups_str.split(';') if component_exclusion_groups_str else []
	component_exclusions = component_exclusions_str.split(';') if component_exclusions_str else []
	frame_components = frame_components_str.split(';') if frame_components_str else []
	while len(taksiya1s) < len(codes):
		taksiya1s.append('0')
	while len(taksiya2s) < len(codes):
		taksiya2s.append('0')
	while len(no_qitaats) < len(codes):
		no_qitaats.append('0')
	while len(net_leafs) < len(codes):
		net_leafs.append('0')
	while len(parquets) < len(codes):
		parquets.append('0')
	while len(square_counts) < len(codes):
		square_counts.append('')
	while len(glass_types) < len(codes):
		glass_types.append('')
	while len(glass_models) < len(codes):
		glass_models.append('')
	while len(component_exclusion_groups) < len(codes):
		component_exclusion_groups.append('')
	while len(component_exclusions) < len(codes):
		component_exclusions.append('')
	while len(frame_components) < len(codes):
		frame_components.append('')

	cutting_cache = {}
	range_cache = {}
	item_group_cache = {}
	component_exclusion_group_cache = {}
	result = {}

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

	def get_item_group(ic):
		if ic not in item_group_cache:
			ig = frappe.db.get_value('Item', ic, 'item_group')
			item_group_cache[ic] = ig or ''
		return item_group_cache[ic]

	def split_widths(total_width, leaf_count, split_type, fixed_leaf_width):
		lc = int(leaf_count or 1) or 1
		total_width = flt(total_width)
		if lc <= 1:
			return [round(total_width, 1)]

		fixed_width = flt(fixed_leaf_width)
		if split_type == 'ثابت' and fixed_width > 0:
			remaining_width = total_width - fixed_width
			other_width = round(remaining_width / (lc - 1), 1)
			return [round(fixed_width, 1)] + [other_width] * (lc - 1)

		each_width = round(total_width / lc, 1)
		return [each_width] * lc

	def split_relative_widths(leaf_total_width, target_total_width, leaf_count, split_type, fixed_leaf_width):
		leaf_parts = split_widths(leaf_total_width, leaf_count, split_type, fixed_leaf_width)
		target_delta = flt(target_total_width) - flt(leaf_total_width)
		target_parts = []
		for leaf_part in leaf_parts:
			target_parts.append(round(leaf_part + target_delta, 1))
		return target_parts

	def split_text(parts):
		if not parts:
			return ""
		formatted = []
		for part in parts:
			formatted.append(format_qty(part))
		return " + ".join(formatted)

	def repeated_size_text(width, repeat_qty):
		repeat_count = int(flt(repeat_qty))
		if repeat_count <= 1 or flt(repeat_qty) != repeat_count:
			return format_qty(width)
		return split_text([width] * repeat_count)

	def split_component_exclusions(value):
		seen = {}
		components = []
		for part in str(value or '').replace('\n', ',').replace(';', ',').split(','):
			component = part.strip()
			if component and component not in seen:
				seen[component] = 1
				components.append(component)
		components.sort()
		return components

	def merge_component_exclusion_lists(*values):
		seen = {}
		components = []
		for value in values:
			for component in value or []:
				component = str(component or '').strip()
				if component and component not in seen:
					seen[component] = 1
					components.append(component)
		components.sort()
		return components

	def component_option_group(component, component_group_map):
		return str((component_group_map or {}).get(str(component or '').strip()) or '').strip()

	def is_same_component_option_group(source_component, target_component, component_group_map):
		source_group = component_option_group(source_component, component_group_map)
		return source_group and source_group == component_option_group(target_component, component_group_map)

	def has_frame_component_range(ranges, component, frame_component, component_group_map):
		frame_component = str(frame_component or '').strip()
		if not frame_component:
			return False
		for rng in ranges:
			if (rng.component or '') != component and not is_same_component_option_group(rng.component, component, component_group_map):
				continue
			if str(rng.get('custom_frame_type') or '').strip() == frame_component:
				return True
		return False

	def add_store_row(stores, row):
		if not row:
			return
		row_component = row.get('component')
		row_item = row.get('item')
		for existing in stores:
			if existing.get('component') == row_component and existing.get('item') == row_item:
				existing['qty'] = flt(existing.get('qty')) + flt(row.get('qty'))
				return
		stores.append(row)

	def get_component_exclusion_group_components(group_name):
		group_name = str(group_name or '').strip()
		if not group_name:
			return []
		if group_name not in component_exclusion_group_cache:
			is_active = frappe.db.get_value('Store Component Exclusion Group', group_name, 'is_active')
			if not int(is_active or 0):
				component_exclusion_group_cache[group_name] = []
			else:
				rows = frappe.db.sql("""
					SELECT store_component
					FROM `tabMaterial Request Item Excluded Store Component`
					WHERE parenttype = 'Store Component Exclusion Group'
					  AND parentfield = 'components'
					  AND parent = %s
					ORDER BY idx
				""", group_name, as_dict=True)
				component_exclusion_group_cache[group_name] = split_component_exclusions(
					','.join([row.store_component or '' for row in rows])
				)
		return component_exclusion_group_cache[group_name]

	def split_panel_widths(leaf_total_width, panel_total_width, leaf_count, split_type, fixed_leaf_width):
		return split_relative_widths(leaf_total_width, panel_total_width, leaf_count, split_type, fixed_leaf_width)

	def has_cb_range(ranges, comp, cb_field):
		for rng in ranges:
			if rng.component != comp:
				continue
			val = 0
			if cb_field == 'taksiya_1':
				val = int(rng.taksiya_1 or 0)
			elif cb_field == 'taksiya_2':
				val = int(rng.taksiya_2 or 0)
			elif cb_field == 'no_qitaat':
				val = int(rng.no_qitaat or 0)
			if val:
				return True
		return False

	def has_square_count_range(ranges, comp):
		for rng in ranges:
			if rng.component != comp:
				continue
			if str(rng.get('custom_square_count') or '').strip():
				return True
		return False

	def has_glass_range(ranges, comp, fieldname):
		for rng in ranges:
			if rng.component != comp:
				continue
			if str(rng.get(fieldname) or '').strip():
				return True
		return False

	def has_sliding_type_range(ranges, comp, sliding_type):
		sliding_type = str(sliding_type or '').strip()
		if not sliding_type:
			return False
		for rng in ranges:
			if rng.component != comp:
				continue
			if str(rng.get('sliding_type') or '').strip() == sliding_type:
				return True
		return False

	def find_range_match(ranges, comp, ic, ig, sl, default_dims, leaf_dims, u_dims, panel_dims, check_ww, tk1=0, tk2=0, noq=0, sq='', glass_type='', glass_model='', frame_component='', component_group_map=None):
		best = None
		best_score = -1
		sl = str(sl or '').strip()
		sq = str(sq or '').strip()
		glass_type = str(glass_type or '').strip()
		glass_model = str(glass_model or '').strip()
		frame_component = str(frame_component or '').strip()
		component_has_requested_sliding = has_sliding_type_range(ranges, comp, sl)
		for rng in ranges:
			if rng.component != comp:
				continue
			ok = True
			score = 0
			# Match checkboxes
			rng_tk1 = int(rng.taksiya_1 or 0)
			rng_tk2 = int(rng.taksiya_2 or 0)
			rng_noq = int(rng.no_qitaat or 0)
			if rng_tk1 and not tk1:
				ok = False
			if rng_tk2 and not tk2:
				ok = False
			if rng_noq and not noq:
				ok = False
			if not rng_tk1 and tk1 and has_cb_range(ranges, comp, 'taksiya_1'):
				ok = False
			if not rng_tk2 and tk2 and has_cb_range(ranges, comp, 'taksiya_2'):
				ok = False
			if not rng_noq and noq and has_cb_range(ranges, comp, 'no_qitaat'):
				ok = False
			rng_sq = str(rng.get('custom_square_count') or '').strip()
			if rng_sq:
				if rng_sq == sq:
					score += 20
				else:
					ok = False
			elif sq and has_square_count_range(ranges, comp):
				ok = False
			rng_glass_type = str(rng.get('custom_glass_type') or '').strip()
			if rng_glass_type:
				if rng_glass_type == glass_type:
					score += 20
				else:
					ok = False
			elif glass_type and has_glass_range(ranges, comp, 'custom_glass_type'):
				ok = False
			rng_glass_model = str(rng.get('custom_glass_model') or '').strip()
			if rng_glass_model:
				if rng_glass_model == glass_model:
					score += 20
				else:
					ok = False
			elif glass_model and has_glass_range(ranges, comp, 'custom_glass_model'):
				ok = False
			rng_frame_component = str(rng.get('custom_frame_type') or '').strip()
			if rng_frame_component:
				if rng_frame_component == frame_component:
					score += 20
				else:
					ok = False
			elif frame_component and has_frame_component_range(ranges, comp, frame_component, component_group_map):
				ok = False
			if not ok:
				continue
			# Match by item_code (highest priority)
			if (rng.item_code or ''):
				if (rng.item_code or '') == ic:
					score += 10
				else:
					ok = False
			# Match by item_group
			elif (rng.item_group or ''):
				if ig and (rng.item_group or '') == ig:
					score += 5
				else:
					ok = False
			rng_sliding_type = str(rng.get('sliding_type') or '').strip()
			if component_has_requested_sliding:
				if rng_sliding_type == sl:
					score += 100
				else:
					ok = False
			elif rng_sliding_type:
				if rng_sliding_type == sl:
					score += 100
				else:
					ok = False
			range_dims = resolve_reference_dims(
				size_reference_value(rng.get('custom_size_reference')),
				default_dims,
				leaf_dims,
				u_dims,
				panel_dims
			)
			range_check_w = range_dims[0]
			range_check_h = range_dims[1]
			if ok and (flt(rng.width_from) > 0 or flt(rng.width_to) > 0) and not (flt(rng.width_from) <= range_check_w and flt(rng.width_to) >= range_check_w):
				ok = False
			if ok and (flt(rng.height_from) > 0 or flt(rng.height_to) > 0) and not (flt(rng.height_from) <= range_check_h and flt(rng.height_to) >= range_check_h):
				ok = False
			if ok and (flt(rng.wall_width_from) > 0 or flt(rng.wall_width_to) > 0) and not (flt(rng.wall_width_from) <= check_ww and flt(rng.wall_width_to) >= check_ww):
				ok = False
			if ok and score > best_score:
				best = rng
				best_score = score
		return best

	def flag_value(val, default_val=1):
		if val is None or val == '':
			return int(default_val)
		try:
			return 1 if int(val) else 0
		except Exception:
			return int(default_val)

	def fallback_value(val):
		text = (val or '').strip().lower()
		if text in ['leaf', 'none']:
			return text
		return 'none'

	def show_with_net_leaf_value(val):
		if val is None or val == '':
			return None
		text = str(val).strip()
		if text == 'يظهر':
			return 1
		if text == 'لا يظهر':
			return 0
		if text == 'نعم':
			return 1
		if text == 'لا':
			return 0
		try:
			return 1 if int(float(text)) else 0
		except Exception:
			return None

	def qty_calc_mode_value(val, per_leaf_flag=0):
		text = (val or '').strip()
		if text == 'لكل درفة':
			text = 'لكل جزء'
		if text == 'بالمعادلة':
			text = 'بالمعادلة على الإجمالي'
		if text in ['ثابت', 'لكل جزء', 'بالمعادلة على الإجمالي', 'بالمعادلة لكل جزء']:
			return text
		return 'لكل جزء' if int(per_leaf_flag or 0) else 'ثابت'

	def quantity_formula_value(val):
		text = (val or '').strip()
		return text

	def uses_part_mode(mode):
		return mode in ['لكل جزء', 'بالمعادلة لكل جزء']

	def safe_formula_text(formula_text):
		text = quantity_formula_value(formula_text).replace('w', 'W').replace('h', 'H')
		if not text:
			return ''
		allowed_chars = '0123456789WH+-*/(). '
		for char in text:
			if char not in allowed_chars:
				return ''
		return text

	def tokenize_formula(formula_text):
		tokens = []
		text = safe_formula_text(formula_text)
		if not text:
			return []
		index = 0
		prev_kind = 'start'
		while index < len(text):
			char = text[index]
			if char == ' ':
				index += 1
				continue
			if char in 'WH':
				tokens.append(char)
				prev_kind = 'value'
				index += 1
				continue
			if char.isdigit() or char == '.' or (char == '-' and prev_kind in ['start', 'operator', '(']):
				start = index
				if char == '-':
					index += 1
				dot_count = 0
				has_digit = False
				while index < len(text) and (text[index].isdigit() or text[index] == '.'):
					if text[index] == '.':
						dot_count += 1
						if dot_count > 1:
							return []
					else:
						has_digit = True
					index += 1
				if not has_digit:
					if text[start] == '-':
						tokens.append(0.0)
						tokens.append('-')
						prev_kind = 'operator'
						continue
					return []
				tokens.append(flt(text[start:index]))
				prev_kind = 'value'
				continue
			if char in '+-*/':
				if prev_kind not in ['value', ')']:
					return []
				tokens.append(char)
				prev_kind = 'operator'
				index += 1
				continue
			if char == '(':
				tokens.append(char)
				prev_kind = '('
				index += 1
				continue
			if char == ')':
				tokens.append(char)
				prev_kind = ')'
				index += 1
				continue
			return []
		return tokens

	def evaluate_tokens(tokens, ref_w, ref_h):
		if not tokens:
			return 0
		precedence = {'+': 1, '-': 1, '*': 2, '/': 2}
		output = []
		operators = []
		for token in tokens:
			if token in ['W', 'H'] or isinstance(token, float):
				output.append(token)
				continue
			if token in precedence:
				while operators and operators[-1] in precedence and precedence[operators[-1]] >= precedence[token]:
					output.append(operators.pop())
				operators.append(token)
				continue
			if token == '(':
				operators.append(token)
				continue
			if token == ')':
				while operators and operators[-1] != '(':
					output.append(operators.pop())
				if not operators or operators[-1] != '(':
					return 0
				operators.pop()
		while operators:
			operator = operators.pop()
			if operator in ['(', ')']:
				return 0
			output.append(operator)

		stack = []
		for token in output:
			if isinstance(token, float):
				stack.append(token)
				continue
			if token == 'W':
				stack.append(flt(ref_w))
				continue
			if token == 'H':
				stack.append(flt(ref_h))
				continue
			if len(stack) < 2:
				return 0
			b = flt(stack.pop())
			a = flt(stack.pop())
			if token == '+':
				stack.append(a + b)
			elif token == '-':
				stack.append(a - b)
			elif token == '*':
				stack.append(a * b)
			elif token == '/':
				if not b:
					return 0
				stack.append(a / b)
		return flt(stack[0]) if len(stack) == 1 else 0

	def evaluate_quantity_formula(formula_text, ref_w, ref_h):
		tokens = tokenize_formula(formula_text)
		return evaluate_tokens(tokens, ref_w, ref_h)

	def size_reference_value(val):
		text = (val or '').strip()
		if text in ['Leaf', 'U', 'Panel']:
			return text
		return ''

	def resolve_reference_dims(reference, default_dims, leaf_dims, u_dims, panel_dims):
		if reference == 'Leaf' and (flt(leaf_dims[0]) or flt(leaf_dims[1])):
			return leaf_dims
		if reference == 'U' and (flt(u_dims[0]) or flt(u_dims[1])):
			return u_dims
		if reference == 'Panel' and (flt(panel_dims[0]) or flt(panel_dims[1])):
			return panel_dims
		return default_dims

	def should_include_component_with_net_leaf(component_rows):
		has_explicit_setting = False
		has_show = False
		has_leaf_mode = False
		for current_row in component_rows:
			behavior = show_with_net_leaf_value(current_row.get('custom_net_leaf_behavior'))
			if behavior is not None:
				has_explicit_setting = True
				if behavior:
					has_show = True
			if uses_part_mode(qty_calc_mode_value(current_row.get('custom_qty_calculation'), current_row.get('per_leaf'))):
				has_leaf_mode = True
		if has_explicit_setting:
			return has_show
		return has_leaf_mode

	def calculate_store_qty(range_row, default_dims, leaf_dims, u_dims, panel_dims):
		mode = qty_calc_mode_value(range_row.get('custom_qty_calculation'), range_row.get('per_leaf'))
		base_qty = flt(range_row.get('qty')) if range_row.get('qty') is not None else 1
		if mode not in ['بالمعادلة على الإجمالي', 'بالمعادلة لكل جزء']:
			return base_qty
		reference = size_reference_value(range_row.get('custom_size_reference'))
		ref_dims = resolve_reference_dims(reference, default_dims, leaf_dims, u_dims, panel_dims)
		ref_w = ref_dims[0]
		ref_h = ref_dims[1]
		if not (flt(ref_w) or flt(ref_h)):
			return 0
		return round(evaluate_quantity_formula(range_row.get('custom_qty_formula'), ref_w, ref_h), 6)

	for i in range(len(codes)):
		ic = codes[i].strip()
		sl = slides[i].strip()
		w_str = widths[i].strip()
		h_str = heights[i].strip()
		ww_str = walls[i].strip()
		w = flt(w_str)
		h = flt(h_str)
		ww = flt(ww_str)
		lc = int(leaf_counts[i].strip() or '1') or 1
		st = split_types[i].strip()
		flw = flt(fixed_lws[i].strip())
		tk1 = int(taksiya1s[i].strip() or '0')
		tk2 = int(taksiya2s[i].strip() or '0')
		noq = int(no_qitaats[i].strip() or '0')
		nl = int(net_leafs[i].strip() or '0')
		pq = int(parquets[i].strip() or '0')
		requested_square_count = square_counts[i].strip()
		requested_glass_type = glass_types[i].strip()
		requested_glass_model = glass_models[i].strip()
		requested_component_exclusion_group = component_exclusion_groups[i].strip()
		requested_frame_component = frame_components[i].strip()
		requested_manual_excluded_components = split_component_exclusions(component_exclusions[i])
		requested_excluded_components = merge_component_exclusion_lists(
			get_component_exclusion_group_components(requested_component_exclusion_group),
			requested_manual_excluded_components
		)
		excluded_component_key = ','.join(requested_excluded_components)
		request_component_exclusion_key = ','.join(requested_manual_excluded_components)
		if not ic:
			continue
		key = ic + '||' + sl + '||' + w_str + '||' + h_str + '||' + ww_str + '||' + str(lc) + '||' + st + '||' + str(flw) + '||' + str(tk1) + '||' + str(tk2) + '||' + str(noq) + '||' + str(nl) + '||' + str(pq) + '||' + requested_square_count + '||' + requested_glass_type + '||' + requested_glass_model + '||' + requested_component_exclusion_group + '||' + request_component_exclusion_key + '||' + requested_frame_component
		if key in result:
			continue

		ig = get_item_group(ic)
		requested_noq = noq

		# Search by item_code first, then by item_group
		if ic not in cutting_cache:
			rows = frappe.db.sql("""
				SELECT cti.leaf_w, cti.leaf_h, cti.u_h, cti.u_w, cti.panel_w, cti.panel_h,
				       cti.notes, cti.sliding_type, cti.type, cti.parent, cti.item_code, cti.item_group,
				       cti.max_width, cti.max_height,
				       cti.default_leaf_count, cti.default_split_type, cti.fixed_leaf_width,
				       cti.parquet_deduction, cti.net_leaf_w_deduction, cti.net_leaf_h_deduction,
				       cti.net_leaf_panel_w, cti.net_leaf_panel_h,
				       cti.net_leaf_u_w, cti.net_leaf_u_h,
				       cti.custom_show_leaf_result, cti.custom_show_u_result, cti.custom_show_panel_result,
				       cti.custom_u_fallback, cti.custom_panel_fallback,
				       cti.custom_allow_missing_dimensions, cti.custom_force_no_qitaat_when_missing_dimensions,
				       cti.custom_dimension_display_mode, cti.custom_dimension_repeat_count,
				       cti.custom_show_square_count, cti.custom_show_glass_options,
				       cti.custom_show_component_exclusions
				FROM `tabCutting Template Item` cti INNER JOIN `tabCutting Template` ct ON ct.name = cti.parent AND IFNULL(ct.disabled, 0) = 0 WHERE cti.item_code = %s
			""", ic, as_dict=True)

			if not rows and ig:
				rows = frappe.db.sql("""
					SELECT cti.leaf_w, cti.leaf_h, cti.u_h, cti.u_w, cti.panel_w, cti.panel_h,
					       cti.notes, cti.sliding_type, cti.type, cti.parent, cti.item_code, cti.item_group,
					       cti.max_width, cti.max_height,
					       cti.default_leaf_count, cti.default_split_type, cti.fixed_leaf_width,
					       cti.parquet_deduction, cti.net_leaf_w_deduction, cti.net_leaf_h_deduction,
					       cti.net_leaf_panel_w, cti.net_leaf_panel_h,
					       cti.net_leaf_u_w, cti.net_leaf_u_h,
					       cti.custom_show_leaf_result, cti.custom_show_u_result, cti.custom_show_panel_result,
					       cti.custom_u_fallback, cti.custom_panel_fallback,
					       cti.custom_allow_missing_dimensions, cti.custom_force_no_qitaat_when_missing_dimensions,
					       cti.custom_dimension_display_mode, cti.custom_dimension_repeat_count,
					       cti.custom_show_square_count, cti.custom_show_glass_options,
					       cti.custom_show_component_exclusions
					FROM `tabCutting Template Item` cti INNER JOIN `tabCutting Template` ct ON ct.name = cti.parent AND IFNULL(ct.disabled, 0) = 0 WHERE cti.item_group = %s
				""", ig, as_dict=True)

			cutting_cache[ic] = rows

		all_cutting = cutting_cache[ic]
		if not all_cutting:
			continue

		best = None
		best_score = -1
		for row in all_cutting:
			score = 0
			ok = True
			if (row.sliding_type or ''):
				if (row.sliding_type or '') == sl:
					score += 1
				else:
					ok = False
			if ok and score > best_score:
				best = row
				best_score = score
		if not best:
			best = all_cutting[0]

		tmpl = best.parent
		show_square_count = flag_value(best.get('custom_show_square_count'), 0)
		show_glass_options = flag_value(best.get('custom_show_glass_options'), 0)
		show_component_exclusions = flag_value(best.get('custom_show_component_exclusions'), 0)
		effective_square_count = requested_square_count if show_square_count else ''
		effective_glass_type = requested_glass_type if show_glass_options else ''
		effective_glass_model = requested_glass_model if show_glass_options else ''
		effective_excluded_components = requested_excluded_components if show_component_exclusions else []
		allow_missing_dimensions = flag_value(best.get('custom_allow_missing_dimensions'), 0)
		force_no_qitaat_when_missing_dimensions = flag_value(best.get('custom_force_no_qitaat_when_missing_dimensions'), 1)
		effective_noq = requested_noq
		if nl:
			effective_noq = 1
		elif w <= 0 or h <= 0:
			if allow_missing_dimensions:
				effective_noq = 1 if force_no_qitaat_when_missing_dimensions else requested_noq
			else:
				effective_noq = 1

		if 'comp_labels' not in range_cache:
			comp_rows = frappe.db.sql("SELECT component_name, label_ar, custom_cutting_option_group FROM `tabStore Component`", as_dict=True)
			range_cache['comp_labels'] = {}
			range_cache['component_groups'] = {}
			for cr in comp_rows:
				range_cache['comp_labels'][cr.component_name] = cr.label_ar or cr.component_name
				if str(cr.get('custom_cutting_option_group') or '').strip():
					range_cache['component_groups'][cr.component_name] = str(cr.get('custom_cutting_option_group') or '').strip()

		if tmpl not in range_cache:
			range_cache[tmpl] = frappe.db.sql("""
				SELECT component, item_code, item_group, sliding_type,
				       width_from, width_to, height_from, height_to,
				       wall_width_from, wall_width_to, item, qty, per_leaf,
				       taksiya_1, taksiya_2, no_qitaat,
				       custom_net_leaf_behavior, custom_qty_calculation, custom_size_reference, custom_qty_formula,
				       custom_square_count, custom_glass_type, custom_glass_model, custom_frame_type
				FROM `tabCutting Wall Range` WHERE parent = %s
				ORDER BY component, item_code
			""", tmpl, as_dict=True)

		all_ranges = range_cache[tmpl]

		comp_list = []
		for rng in all_ranges:
			if rng.component and rng.component not in comp_list:
				comp_list.append(rng.component)
		if effective_excluded_components:
			comp_list = [c for c in comp_list if c not in effective_excluded_components]

		show_leaf = flag_value(best.get('custom_show_leaf_result'), 1)
		show_u = flag_value(best.get('custom_show_u_result'), 1)
		show_panel = flag_value(best.get('custom_show_panel_result'), 1)
		u_fallback = fallback_value(best.get('custom_u_fallback'))
		panel_fallback = fallback_value(best.get('custom_panel_fallback'))
		if pq and flt(best.parquet_deduction):
			h = h + flt(best.parquet_deduction)

		# Calculate dimensions for matching, respecting net leaf mode.
		if nl:
			lw = w + flt(best.net_leaf_w_deduction)
			lh = h + flt(best.net_leaf_h_deduction)
			if show_u:
				uw = w + flt(best.net_leaf_u_w) if flt(best.net_leaf_u_w) else (lw if u_fallback == 'leaf' else 0)
				uh = h + flt(best.net_leaf_u_h) if flt(best.net_leaf_u_h) else (lh if u_fallback == 'leaf' else 0)
			else:
				uw = 0
				uh = 0
			if show_panel:
				pw = w + flt(best.net_leaf_panel_w) if flt(best.net_leaf_panel_w) else (lw if panel_fallback == 'leaf' else 0)
				ph = h + flt(best.net_leaf_panel_h) if flt(best.net_leaf_panel_h) else (lh if panel_fallback == 'leaf' else 0)
			else:
				pw = 0
				ph = 0
		else:
			lw = w + flt(best.leaf_w) if flt(best.leaf_w) else w
			lh = h + flt(best.leaf_h) if flt(best.leaf_h) else h
			if show_u:
				uw = w + flt(best.u_w) if flt(best.u_w) else (lw if u_fallback == 'leaf' else 0)
				uh = h + flt(best.u_h) if flt(best.u_h) else (lh if u_fallback == 'leaf' else 0)
			else:
				uw = 0
				uh = 0
			if show_panel:
				pw = w + flt(best.panel_w) if flt(best.panel_w) else (lw if panel_fallback == 'leaf' else 0)
				ph = h + flt(best.panel_h) if flt(best.panel_h) else (lh if panel_fallback == 'leaf' else 0)
			else:
				pw = 0
				ph = 0
		has_panel = 1 if show_panel and (pw or ph) else 0
		dimension_display_mode = (best.get('custom_dimension_display_mode') or '').strip()
		dimension_repeat_qty = int(flt(best.get('custom_dimension_repeat_count')) or 0)
		repeat_same_dimension = dimension_display_mode == 'تكرار نفس المقاس' and dimension_repeat_qty > 1
		dimension_leaf_count = 1 if repeat_same_dimension else lc

		if show_leaf and lw >= 0:
			leaf_w_text = split_text(split_widths(lw, dimension_leaf_count, st, flw)) if dimension_leaf_count > 1 else format_qty(lw)
		else:
			leaf_w_text = ""

		if show_u and uw > 0:
			u_w_text = split_text(split_relative_widths(lw, uw, dimension_leaf_count, st, flw)) if dimension_leaf_count > 1 else format_qty(uw)
		else:
			u_w_text = ""

		if show_panel and pw > 0:
			panel_w_text = split_text(split_panel_widths(lw, pw, dimension_leaf_count, st, flw)) if dimension_leaf_count > 1 else format_qty(pw)
		else:
			panel_w_text = ""

		stores = []
		component_group_map = range_cache.get('component_groups') or {}
		if not repeat_same_dimension:
			dimension_repeat_qty = 1
		item_name_cache = {}
		component_rows_map = {}
		for current_component in comp_list:
			component_rows_map[current_component] = [rng for rng in all_ranges if rng.component == current_component]
		if nl:
			comp_list = [c for c in comp_list if should_include_component_with_net_leaf(component_rows_map.get(c, []))]
		elif effective_noq:
			part_mode_comps = []
			for c in comp_list:
				component_rows = component_rows_map.get(c, [])
				if any(uses_part_mode(qty_calc_mode_value(rng.get('custom_qty_calculation'), rng.get('per_leaf'))) for rng in component_rows):
					part_mode_comps.append(c)
			comp_list = part_mode_comps
		for comp in comp_list:
			component_rows = component_rows_map.get(comp, [])
			component_has_part_mode = any(uses_part_mode(qty_calc_mode_value(rng.get('custom_qty_calculation'), rng.get('per_leaf'))) for rng in component_rows)
			if lc > 1 and component_has_part_mode:
				leaf_parts = split_widths(lw, lc, st, flw)
				u_parts = split_relative_widths(lw, uw, lc, st, flw) if flt(uw) else [0] * len(leaf_parts)
				panel_parts = split_panel_widths(lw, pw, lc, st, flw) if flt(pw) else [0] * len(leaf_parts)
				part_groups = {}
				for part_index in range(len(leaf_parts)):
					part_leaf_dims = (leaf_parts[part_index], lh)
					part_u_dims = (u_parts[part_index], uh)
					part_panel_dims = (panel_parts[part_index], ph)
					part_default_dims = part_panel_dims
					m = find_range_match(
						all_ranges,
						comp,
						ic,
						ig,
						sl,
						part_default_dims,
						part_leaf_dims,
						part_u_dims,
						part_panel_dims,
						ww,
						tk1,
						tk2,
						effective_noq,
						effective_square_count,
						effective_glass_type,
						effective_glass_model,
						requested_frame_component,
						component_group_map
					)
					if m:
						mk = m.item or ''
						add_qty = calculate_store_qty(m, part_default_dims, part_leaf_dims, part_u_dims, part_panel_dims)
						if mk not in part_groups:
							if mk not in item_name_cache:
								item_name_cache[mk] = frappe.db.get_value('Item', mk, 'item_name') if mk else ''
							part_groups[mk] = {'component': comp, 'component_ar': range_cache['comp_labels'].get(comp, comp), 'item': mk, 'item_name': item_name_cache[mk] or mk, 'qty': add_qty}
						else:
							part_groups[mk]['qty'] = part_groups[mk]['qty'] + add_qty
				for pg in part_groups.values():
					add_store_row(stores, pg)
			else:
				search_w = w
				search_h = h
				default_dims = (search_w, search_h)
				leaf_dims = (lw, lh)
				u_dims = (uw, uh)
				panel_dims = (pw, ph)
				match = find_range_match(all_ranges, comp, ic, ig, sl, default_dims, leaf_dims, u_dims, panel_dims, ww, tk1, tk2, effective_noq, effective_square_count, effective_glass_type, effective_glass_model, requested_frame_component, component_group_map)
				if match:
					mk = match.item or ''
					if mk not in item_name_cache:
						item_name_cache[mk] = frappe.db.get_value('Item', mk, 'item_name') if mk else ''
					match_qty = calculate_store_qty(match, default_dims, leaf_dims, u_dims, panel_dims)
					add_store_row(stores, {
						'component': comp,
						'component_ar': range_cache['comp_labels'].get(comp, comp),
						'item': mk,
						'item_name': item_name_cache[mk] or mk,
						'qty': match_qty
					})

		if dimension_repeat_qty > 1:
			if show_leaf and flt(lw) > 0:
				leaf_w_text = repeated_size_text(lw, dimension_repeat_qty)
			if show_u and flt(uw) > 0:
				u_w_text = repeated_size_text(uw, dimension_repeat_qty)
			if show_panel and flt(pw) > 0:
				panel_w_text = repeated_size_text(pw, dimension_repeat_qty)

		item_opts = []
		for row in all_cutting:
			if row.parent != best.parent:
				continue
			if (row.sliding_type or '') and row.sliding_type not in item_opts:
				item_opts.append(row.sliding_type)

		entry = {
			'leaf_w': best.leaf_w, 'leaf_h': best.leaf_h,
			'u_w': best.u_w, 'u_h': best.u_h,
			'panel_w': best.panel_w, 'panel_h': best.panel_h,
			'result_leaf_w': lw if show_leaf else 0,
			'result_leaf_h': lh if show_leaf else 0,
			'result_u_w': uw if show_u else 0,
			'result_u_h': uh if show_u else 0,
			'result_panel_w': pw if show_panel else 0,
			'result_panel_h': ph if show_panel else 0,
			'type': best.type or '',
			'parent': best.parent,
			'max_width': best.max_width, 'max_height': best.max_height,
			'default_leaf_count': best.default_leaf_count or '1',
			'default_split_type': best.default_split_type or '',
			'fixed_leaf_width': flt(best.fixed_leaf_width),
			'parquet_deduction': flt(best.parquet_deduction),
			'net_leaf_w_deduction': flt(best.net_leaf_w_deduction),
			'net_leaf_h_deduction': flt(best.net_leaf_h_deduction),
			'net_leaf_panel_w': flt(best.net_leaf_panel_w),
			'net_leaf_panel_h': flt(best.net_leaf_panel_h),
			'net_leaf_u_w': flt(best.net_leaf_u_w),
			'net_leaf_u_h': flt(best.net_leaf_u_h),
			'leaf_w_text': leaf_w_text,
			'u_w_text': u_w_text,
			'panel_w_text': panel_w_text,
			'show_leaf_result': show_leaf,
			'show_u_result': show_u,
			'show_panel_result': show_panel,
			'u_fallback': u_fallback,
			'panel_fallback': panel_fallback,
			'allow_missing_dimensions': allow_missing_dimensions,
			'effective_no_qitaat': effective_noq,
			'square_count': effective_square_count,
			'show_square_count': show_square_count,
			'show_glass_options': show_glass_options,
			'show_component_exclusions': show_component_exclusions,
			'excluded_components': ','.join(effective_excluded_components),
			'item_sliding_options': item_opts,
			'stores': stores
		}
		result[key] = entry

	frappe.response['message'] = {'values': result}
