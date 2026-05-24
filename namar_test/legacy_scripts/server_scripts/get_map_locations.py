# API: get_map_locations

def clean_text(value):
    if value is None:
        return ''
    return str(value).strip()


def add_unique(items, seen, value):
    value = clean_text(value)
    if value and value not in seen:
        items.append(value)
        seen[value] = 1


def parse_status_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    value = clean_text(value)
    if not value:
        return []
    if value.startswith('['):
        try:
            parsed = frappe.parse_json(value)
            if isinstance(parsed, list):
                return [clean_text(item) for item in parsed if clean_text(item)]
        except Exception:
            pass
    return [value]


def has_mr_field(fieldname):
    if not fieldname:
        return False
    try:
        if frappe.get_meta('Material Request').has_field(fieldname):
            return True
    except Exception:
        pass
    try:
        return bool(frappe.db.exists('Custom Field', 'Material Request-' + fieldname))
    except Exception:
        return False


def get_branch_field():
    for fieldname in ['الفرع', 'branch']:
        if has_mr_field(fieldname):
            return fieldname
    return ''


def get_branch_options(data_branches):
    branch_set = {}
    for item in data_branches:
        branch = clean_text(item)
        if branch:
            branch_set[branch] = 1

    ordered = []
    seen = {}
    try:
        if frappe.db.exists('DocType', 'Branch'):
            rows = frappe.get_all(
                'Branch',
                fields=['name'],
                order_by='name asc',
                limit_page_length=0,
            )
            for row in rows:
                name = clean_text(row.get('name'))
                if name in branch_set:
                    add_unique(ordered, seen, name)
    except Exception:
        pass

    for item in sorted(branch_set):
        add_unique(ordered, seen, item)
    return ordered


def get_territory_options():
    if not frappe.db.exists('DocType', 'Territory'):
        return []

    fields = ['name', 'lft']
    try:
        if frappe.get_meta('Territory').has_field('custom_display_order'):
            fields.append('custom_display_order')
    except Exception:
        pass

    rows = frappe.get_all(
        'Territory',
        filters={'is_group': 0},
        fields=fields,
        order_by='lft asc, creation asc',
        limit_page_length=0,
    )

    def sort_key(row):
        order = row.get('custom_display_order')
        try:
            order = int(order)
        except Exception:
            order = None
        if order is not None and order <= 0:
            order = None
        return (
            0 if order is not None else 1,
            order if order is not None else 0,
            row.get('lft') or 0,
            clean_text(row.get('name')),
        )

    return [clean_text(row.get('name')) for row in sorted(rows, key=sort_key) if clean_text(row.get('name'))]


territory = frappe.form_dict.get('territory', '')
team = frappe.form_dict.get('team', '')
branch = frappe.form_dict.get('branch', '')
status = frappe.form_dict.get('status', '')
from_date = frappe.form_dict.get('from_date', '')
to_date = frappe.form_dict.get('to_date', '')
branch_field = get_branch_field()

filters = {
    'docstatus': ['in', [0, 1]]
}

if territory:
    filters['territory'] = territory

if team:
    filters['custom_installation_note_teams'] = team

if branch and branch_field:
    filters[branch_field] = branch

if from_date:
    filters['transaction_date'] = ['>=', from_date]

if to_date:
    if 'transaction_date' in filters:
        filters['transaction_date'] = ['between', [from_date, to_date]]
    else:
        filters['transaction_date'] = ['<=', to_date]

fields = [
    'name', 'customer_name', 'territory', 'custom_installation_note_teams',
    'custom_mobile_no', 'delivery_date',
    'workflow_state', 'custom_google_map',
    'custom_latitude', 'custom_longitude',
    'sales_order', 'transaction_date'
]
if branch_field and branch_field not in fields:
    fields.append(branch_field)

orders = frappe.get_all('Material Request',
    filters=filters,
    fields=fields,
    order_by='transaction_date desc',
    limit_page_length=0
)

territories = []
teams = []
branches = []
statuses = []
seen_t = {}
seen_tm = {}
seen_b = {}
seen_s = {}

for o in orders:
    branch_value = clean_text(o.get(branch_field)) if branch_field else ''
    o['branch'] = branch_value
    add_unique(territories, seen_t, o.get('territory'))
    add_unique(teams, seen_tm, o.get('custom_installation_note_teams'))
    add_unique(branches, seen_b, branch_value)
    add_unique(statuses, seen_s, o.get('workflow_state'))

territory_options = get_territory_options()
seen_option = {}
ordered_territories = []
for item in territory_options:
    add_unique(ordered_territories, seen_option, item)
for item in territories:
    add_unique(ordered_territories, seen_option, item)
if not ordered_territories:
    ordered_territories = sorted(territories)

status_list = parse_status_list(status)

count_filters = {
    'docstatus': ['in', [0, 1]]
}
if from_date and to_date:
    count_filters['transaction_date'] = ['between', [from_date, to_date]]
elif from_date:
    count_filters['transaction_date'] = ['>=', from_date]
elif to_date:
    count_filters['transaction_date'] = ['<=', to_date]
if team:
    count_filters['custom_installation_note_teams'] = team
if branch and branch_field:
    count_filters[branch_field] = branch

all_for_counts = frappe.get_all('Material Request',
    filters=count_filters,
    fields=['territory', 'workflow_state'],
    limit_page_length=0
)

territory_counts_raw = {}
for o in all_for_counts:
    t = clean_text(o.get('territory'))
    s = clean_text(o.get('workflow_state'))
    if not t:
        continue
    if status_list and s not in status_list:
        continue
    territory_counts_raw[t] = territory_counts_raw.get(t, 0) + 1

territory_counts = {}
for item in ordered_territories:
    if item in territory_counts_raw:
        territory_counts[item] = territory_counts_raw[item]
for item in sorted(territory_counts_raw):
    if item not in territory_counts:
        territory_counts[item] = territory_counts_raw[item]

if status_list:
    orders = [o for o in orders if o.get('workflow_state') in status_list]

frappe.response['message'] = {
    'orders': orders,
    'filters_data': {
        'territories': ordered_territories,
        'teams': sorted(teams),
        'branches': get_branch_options(branches),
        'branch_field': branch_field,
        'statuses': sorted(statuses)
    },
    'territory_counts': territory_counts
}
