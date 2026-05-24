def extract_number(text):
    result = ''
    for ch in text:
        if ch in '0123456789.-':
            result = result + ch
        else:
            if result:
                break
    if result and result != '-' and result != '.':
        return float(result)
    return 0

orders = frappe.get_all('Material Request',
    filters={
        'docstatus': 1,
        'custom_google_map': ['like', '%maps%'],
        'custom_latitude': ['<', 0.1]
    },
    fields=['name', 'custom_google_map'],
    limit_page_length=50
)

updated = 0
errors = 0

for order in orders:
    url = order.custom_google_map or ''
    lat = 0
    lng = 0

    # Method 1: Extract from q= parameter directly in URL
    if 'q=' in url:
        try:
            part = url.split('q=')[1]
            if '&' in part:
                part = part.split('&')[0]
            part = part.replace('%2C', ',')
            if ',' in part:
                pieces = part.split(',')
                test_lat = extract_number(pieces[0])
                test_lng = extract_number(pieces[1])
                if test_lat > 1 and test_lng > 1:
                    lat = test_lat
                    lng = test_lng
        except Exception:
            pass

    # Method 2: Extract from @ in URL
    if lat == 0 and '@' in url:
        try:
            part = url.split('@')[1]
            if ',' in part:
                pieces = part.split(',')
                test_lat = extract_number(pieces[0])
                test_lng = extract_number(pieces[1])
                if test_lat > 1 and test_lng > 1:
                    lat = test_lat
                    lng = test_lng
        except Exception:
            pass

    # Method 3: Extract !3d !4d from URL directly
    if lat == 0 and '!3d' in url and '!4d' in url:
        try:
            lat = extract_number(url.split('!3d')[1])
            lng = extract_number(url.split('!4d')[1])
        except Exception:
            pass

    # Method 4: For short links, try HTTP request and parse response OR error
    if lat == 0 and 'goo.gl' in url:
        full = ''
        try:
            result = frappe.make_get_request(url, headers={'User-Agent': 'Mozilla/5.0'})
            full = str(result)
        except Exception as e:
            full = str(e)

        # Try !3d and !4d pattern
        if lat == 0 and '!3d' in full and '!4d' in full:
            try:
                lat = extract_number(full.split('!3d')[1])
                lng = extract_number(full.split('!4d')[1])
            except Exception:
                pass

        # Try %40 (encoded @) with lat,lng
        if lat == 0 and '%40' in full:
            try:
                part = full.split('%40')[1]
                part = part.replace('%2C', ',')
                if ',' in part:
                    pieces = part.split(',')
                    test_lat = extract_number(pieces[0])
                    test_lng = extract_number(pieces[1])
                    if test_lat > 1 and test_lng > 1:
                        lat = test_lat
                        lng = test_lng
            except Exception:
                pass

        # Try @ pattern
        if lat == 0 and '@' in full:
            try:
                part = full.split('@')[1]
                if ',' in part:
                    pieces = part.split(',')
                    test_lat = extract_number(pieces[0])
                    test_lng = extract_number(pieces[1])
                    if test_lat > 1 and test_lng > 1:
                        lat = test_lat
                        lng = test_lng
            except Exception:
                pass

    if lat != 0 and lng != 0:
        frappe.db.set_value('Material Request', order.name, {
            'custom_latitude': lat,
            'custom_longitude': lng
        })
        updated = updated + 1
    else:
        errors = errors + 1

frappe.db.commit()

remaining = frappe.db.count('Material Request', {
    'docstatus': 1,
    'custom_google_map': ['like', '%maps%'],
    'custom_latitude': ['<', 0.1]
})

frappe.response['message'] = {
    'updated': updated,
    'errors': errors,
    'remaining': remaining
}
