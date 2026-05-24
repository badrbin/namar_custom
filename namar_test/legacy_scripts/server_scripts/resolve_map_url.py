# resolve_map_url - Extract coordinates from Google Maps URLs and Plus Codes
# Supports: direct coords, shortlinks, plus codes, query geocoding via Google API
GMAPS_KEY = 'AIzaSyAcKZfM2PK4FBrKEB56Ls2Il7urmew4Ajk'

raw_input = frappe.form_dict.get('url', '')


def is_valid(lat, lng):
    return 15 <= lat <= 33 and 34 <= lng <= 57


def parse_float(value):
    try:
        return float((value or '').strip())
    except Exception:
        return None


def try_parse_pair(text):
    parts = (text or '').strip().split(',')
    if len(parts) != 2:
        return None
    lat = parse_float(parts[0])
    lng = parse_float(parts[1])
    if lat is None or lng is None:
        return None
    if not is_valid(lat, lng):
        return None
    return {'lat': lat, 'lng': lng, 'method': 'direct'}


def find_param(text, param):
    idx = text.find(param + '=')
    if idx < 0:
        return ''
    rest = text[idx + len(param) + 1:]
    for delim in ['&', '#', ' ', ';']:
        pos = rest.find(delim)
        if pos >= 0:
            rest = rest[:pos]
    return rest.strip()


def find_first_param(text, names):
    for name in names:
        value = find_param(text, name)
        if value:
            return value
    return ''


def decode_loose(text):
    value = (text or '').strip()
    value = value.replace('%2B', '+').replace('%2b', '+')
    value = value.replace('%2C', ',').replace('%2c', ',')
    value = value.replace('%20', ' ').replace('+', ' ')
    return value.strip()


def encode_for_geocode(text):
    value = (text or '').strip()
    if not value:
        return ''
    value = value.replace('%2B', '__PLUS__').replace('%2b', '__PLUS__')
    value = value.replace('+', '%2B')
    value = value.replace(' ', '+')
    value = value.replace(',', '%2C')
    value = value.replace('#', '%23')
    value = value.replace('&', '%26')
    value = value.replace('__PLUS__', '%2B')
    return value


def looks_like_plus_code(text):
    value = (text or '').strip().upper()
    if '+' not in value:
        return False
    left, right = value.split('+', 1)
    if len(left) < 2 or len(right) < 2:
        return False
    allowed = '23456789CFGHJMPQRVWX'
    valid_left = True
    for char in left:
        if char not in allowed:
            valid_left = False
            break
    if not valid_left:
        return False
    valid_right = True
    for char in right[:2]:
        if char not in allowed:
            valid_right = False
            break
    return valid_right


def extract_3d4d(text):
    idx = text.find('!3d')
    if idx < 0:
        return None
    rest = text[idx + 3:]
    end = 0
    for char in rest:
        if char in '0123456789.-':
            end += 1
        else:
            break
    if end <= 0:
        return None
    lat_str = rest[:end]
    rest2 = rest[end:]
    if not rest2.startswith('!4d'):
        return None
    rest3 = rest2[3:]
    end2 = 0
    for char in rest3:
        if char in '0123456789.-':
            end2 += 1
        else:
            break
    if end2 <= 0:
        return None
    lat = parse_float(lat_str)
    lng = parse_float(rest3[:end2])
    if lat is None or lng is None or not is_valid(lat, lng):
        return None
    return {'lat': lat, 'lng': lng, 'method': '3d4d'}


def extract_from_place_path(text):
    marker = '/place/'
    idx = text.find(marker)
    if idx < 0:
        return None
    rest = text[idx + len(marker):]
    slash = rest.find('/')
    if slash >= 0:
        rest = rest[slash + 1:]
    end = 0
    for char in rest:
        if char in '0123456789.,-':
            end += 1
        else:
            break
    if end <= 0:
        return None
    pair = try_parse_pair(rest[:end])
    if pair:
        pair['method'] = 'place_path'
    return pair


def extract(text):
    text = decode_loose(text)
    if not text:
        return None

    pair = try_parse_pair(text)
    if pair:
        return pair

    for param in ['q', 'query', 'destination', 'daddr', 'll', 'saddr']:
        value = decode_loose(find_param(text, param))
        pair = try_parse_pair(value)
        if pair:
            pair['method'] = 'param:' + param
            return pair

    at_idx = text.find('@')
    if at_idx >= 0:
        rest = text[at_idx + 1:]
        end = 0
        for char in rest:
            if char in '0123456789.,-':
                end += 1
            else:
                break
        if end > 0:
            pair = try_parse_pair(rest[:end])
            if pair:
                pair['method'] = 'at_pair'
                return pair

    pair = extract_3d4d(text)
    if pair:
        return pair

    pair = extract_from_place_path(text)
    if pair:
        return pair

    return None


def extract_address(text):
    value = (text or '').strip()
    if not value:
        return ''

    raw_param = find_first_param(value, ['query', 'q', 'destination', 'daddr'])
    if raw_param:
        decoded = decode_loose(raw_param)
        if not try_parse_pair(decoded):
            return raw_param

    decoded_value = decode_loose(value)
    if decoded_value and not try_parse_pair(decoded_value):
        if looks_like_plus_code(decoded_value):
            return decoded_value
        if value.find('http://') != 0 and value.find('https://') != 0:
            return value

    return ''


def geocode_address(address):
    if not address or not GMAPS_KEY or GMAPS_KEY == 'AIzaSyAcKZfM2PK4FBrKEB56Ls2Il7urmew4Ajk':
        return None
    try:
        encoded = encode_for_geocode(address)
        api_url = 'https://maps.googleapis.com/maps/api/geocode/json?address=' + encoded + '&region=sa&key=' + GMAPS_KEY
        resp = frappe.make_get_request(api_url)
        if resp and resp.get('status') == 'REQUEST_DENIED':
            return {
                'lat': None,
                'lng': None,
                'method': 'geocode_denied',
                'error': resp.get('error_message') or 'Google Geocoding API request denied',
            }
        if resp and resp.get('status') == 'OK' and resp.get('results'):
            loc = resp['results'][0]['geometry']['location']
            lat = loc.get('lat')
            lng = loc.get('lng')
            if lat is not None and lng is not None and is_valid(lat, lng):
                return {'lat': lat, 'lng': lng, 'method': 'geocode'}
    except Exception:
        pass
    return None


def resolve_text(value):
    result = extract(value)
    if result:
        return result
    address = extract_address(value)
    if address:
        return geocode_address(address)
    return None


result = None

if raw_input:
    raw_input = raw_input.strip()
    result = resolve_text(raw_input)

    if not result and ('goo.gl' in raw_input or 'maps.app' in raw_input):
        try:
            response_text = frappe.make_get_request(raw_input, headers={'User-Agent': 'Mozilla/5.0'})
            if response_text:
                response_text = str(response_text)
                result = resolve_text(response_text)
        except Exception:
            pass

if result:
    frappe.response['message'] = result
else:
    frappe.response['message'] = {'lat': None, 'lng': None, 'method': 'unresolved'}
