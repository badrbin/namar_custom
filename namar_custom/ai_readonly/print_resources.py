"""Inspect rendered print resources before starting the PDF renderer."""
from __future__ import annotations

import html
import base64
from io import BytesIO
from html.parser import HTMLParser
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urljoin, urlsplit

from .policy import Denied

MAX_IMAGE_BYTES = 32 * 1024 * 1024


def _url(value):
    value = html.unescape(str(value).strip())
    if "\\" in value or any(ord(char) < 32 for char in value):
        raise Denied("Ambiguous print resource URL")
    return value


def _decoded(value):
    value = _url(value)
    for _ in range(4):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    if "\\" in value or any(ord(char) < 32 for char in value):
        raise Denied("Ambiguous print resource URL")
    return value


def validate_html(content, site_url, external_origins):
    """Only passive assets and explicitly reviewed external image endpoints."""
    site = urlsplit(site_url)

    def resource(value, *, stylesheet=False):
        value = _url(value)
        if value.startswith("#"):
            return
        # SVG data can itself contain external resources. Permit passive raster
        # data here; inline SVG markup is inspected by the HTML parser below.
        if re.match(r"^data:image/(png|jpeg|jpg|gif|webp);base64,", value, re.IGNORECASE):
            if stylesheet:
                raise Denied("A stylesheet cannot be an image")
            return
        parsed = urlsplit(urljoin(site_url.rstrip("/") + "/", value))
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise Denied("Print resource scheme is not approved")
        path = _decoded(parsed.path)
        normalized = posixpath.normpath(path)
        if not path.startswith("/") or normalized.rstrip("/") != path.rstrip("/") or "//" in path:
            raise Denied("Noncanonical print resource path")
        if path == "/api" or path.startswith("/api/"):
            raise Denied("API requests are not print resources")
        origin = f"{parsed.scheme}://{parsed.netloc.lower()}"
        if parsed.netloc.lower() == site.netloc.lower():
            allowed = ("/assets/",) if stylesheet else ("/assets/", "/files/", "/private/files/")
            if any(path.startswith(prefix) for prefix in allowed):
                return
            raise Denied("Local print URL is outside assets/files")
        if stylesheet:
            raise Denied("Remote stylesheets are not approved")
        if parsed.scheme == "https" and any(path == prefix or path.startswith(prefix.rstrip("/") + "/")
            for prefix in external_origins.get(origin, [])):
            return
        raise Denied("External print resource has not been reviewed")

    def css(value):
        # Decode CSS escapes before URL/import matching so u\72l cannot conceal
        # a resource. Reject comments after decoding instead of treating them as
        # executable token glue. Native print CSS comments are removed safely.
        value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
        value = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", lambda match: chr(int(match.group(1), 16)), value)
        value = re.sub(r"\\([^\r\n0-9a-fA-F])", r"\1", value)
        if "\\" in value or re.search(r"(?:expression|behavior|image-set)\s*[:(]", value, re.I):
            raise Denied("Unreviewed CSS resource syntax")
        for match in re.finditer(r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^)]*))\s*\)", value, re.I):
            resource(next(item for item in match.groups() if item is not None).strip())
        for match in re.finditer(r"@import\s+(?:\"([^\"]*)\"|'([^']*)'|url\(([^)]*)\))", value, re.I):
            resource(next(item for item in match.groups() if item is not None).strip(" \"'"), stylesheet=True)

    class Resources(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.in_style = False

        def handle_starttag(self, tag, attributes):
            attrs = dict(attributes)
            if tag in ("iframe", "object", "embed", "base", "foreignobject", "animate", "set", "animatemotion", "animatetransform",
                       "video", "audio", "source", "track"):
                raise Denied("Active embedded print document")
            if tag == "meta" and (str(attrs.get("http-equiv", "")).lower() == "refresh"
                                   or str(attrs.get("name", "")).lower().startswith("pdfkit-")):
                raise Denied("Print navigation or renderer override")
            if attrs.get("srcset"):
                raise Denied("Multiple-source image syntax requires a reviewed adapter")
            if tag == "style":
                self.in_style = True
            for key in ("src", "poster", "background"):
                if attrs.get(key):
                    resource(attrs[key])
            if tag == "link" and attrs.get("href"):
                resource(attrs["href"], stylesheet="stylesheet" in str(attrs.get("rel", "")).lower())
            if tag not in ("a", "link"):
                for key in ("href", "xlink:href"):
                    if attrs.get(key):
                        resource(attrs[key])
            if attrs.get("style"):
                css(attrs["style"])

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

        def handle_endtag(self, tag):
            if tag == "style":
                self.in_style = False

        def handle_data(self, data):
            if self.in_style:
                css(data)

    parser = Resources()
    parser.feed(content)
    parser.close()


def _raster_data(content):
    from PIL import Image, UnidentifiedImageError
    if not isinstance(content, bytes) or len(content) > MAX_IMAGE_BYTES:
        raise Denied("Print image is not a bounded binary resource")
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in ("PNG", "JPEG", "GIF", "WEBP") or source.width * source.height > 64_000_000:
                raise Denied("Only reviewed raster image formats are enabled")
            source.load()
            output = BytesIO()
            # Re-encoding drops XML, metadata and trailing/polyglot content.
            source.convert("RGBA").save(output, format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise Denied("SVG/XML or invalid raster print image") from exc
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def _fetch_image(url):
    import requests
    # Never forward ERP cookies or credentials to an image service. A redirect
    # is an error, so an approved QR URL cannot redirect wkhtmltopdf to /api.
    with requests.get(url, allow_redirects=False, stream=True, timeout=(5, 20)) as response:
        if response.status_code != 200:
            raise Denied("Print image fetch failed or attempted a redirect")
        content = bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if len(content) > MAX_IMAGE_BYTES:
                raise Denied("Print image exceeds the size limit")
        return bytes(content)


def _local_image(path):
    import frappe
    if path.startswith("/assets/"):
        parts = path.lstrip("/").split("/")
        if len(parts) < 3 or parts[1] not in frappe.get_installed_apps():
            raise Denied("Asset image is outside the pinned installed applications")
        location = Path(frappe.local.sites_path) / path.lstrip("/")
        if not location.is_file() or location.stat().st_size > MAX_IMAGE_BYTES:
            raise Denied("Pinned asset image is unavailable or too large")
        return location.read_bytes()
    from frappe.core.doctype.file.utils import find_file_by_url
    file = find_file_by_url(path)
    if file is None:
        raise Denied("Print file is not downloadable by this user")
    content = file.get_content()
    return content.encode() if isinstance(content, str) else content


def inline_images(content, site_url, external_origins, load_local=None, fetch_remote=None):
    """Resolve images ourselves; the PDF process receives passive raster data."""
    from bs4 import BeautifulSoup
    validate_html(content, site_url, external_origins)
    load_local = load_local or _local_image
    fetch_remote = fetch_remote or _fetch_image
    site = urlsplit(site_url)
    cache = {}

    def convert(value):
        if value.startswith("#"):
            return value
        if value in cache:
            return cache[value]
        if value.lower().startswith("data:image/"):
            try:
                body = base64.b64decode(value.split(",", 1)[1], validate=True)
            except (ValueError, IndexError) as exc:
                raise Denied("Invalid embedded raster image") from exc
        else:
            parsed = urlsplit(urljoin(site_url.rstrip("/") + "/", _url(value)))
            body = load_local(_decoded(parsed.path)) if parsed.netloc.lower() == site.netloc.lower() else fetch_remote(parsed.geturl())
        cache[value] = _raster_data(body)
        return cache[value]

    def css_images(value):
        decoded = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
        decoded = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", lambda match: chr(int(match.group(1), 16)), decoded)
        decoded = re.sub(r"\\([^\r\n0-9a-fA-F])", r"\1", decoded)
        def replace(match):
            url = next(item for item in match.groups() if item is not None).strip()
            parsed = urlsplit(urljoin(site_url.rstrip("/") + "/", _url(url)))
            # CSS/fonts inside pinned app assets remain passive reviewed assets.
            # User files and external QR resources must be rasterized instead.
            if parsed.netloc.lower() == site.netloc.lower() and parsed.path.startswith("/assets/"):
                return match.group(0)
            return 'url("' + convert(url) + '")'
        return re.sub(r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^)]*))\s*\)", replace, decoded, flags=re.I)

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.find_all(True):
        for key in ("background", "poster"):
            if tag.get(key):
                tag[key] = convert(tag[key])
        if tag.name in ("img", "input") and tag.get("src"):
            tag["src"] = convert(tag["src"])
        elif tag.get("src"):
            parsed = urlsplit(urljoin(site_url.rstrip("/") + "/", _url(tag["src"])))
            if parsed.netloc.lower() != site.netloc.lower() or not parsed.path.startswith("/assets/"):
                raise Denied("Non-image print sources must be pinned app assets")
        if tag.name == "link" and tag.get("href"):
            if "icon" in tag.get("rel", []):
                tag["href"] = convert(tag["href"])
            else:
                parsed = urlsplit(urljoin(site_url.rstrip("/") + "/", _url(tag["href"])))
                if parsed.netloc.lower() != site.netloc.lower() or not parsed.path.startswith("/assets/"):
                    raise Denied("Print link resources must be pinned app assets")
        if tag.name == "use":
            for key in ("href", "xlink:href"):
                if tag.get(key) and not tag[key].startswith("#"):
                    parsed = urlsplit(urljoin(site_url.rstrip("/") + "/", _url(tag[key])))
                    if parsed.netloc.lower() != site.netloc.lower() or not parsed.path.startswith("/assets/"):
                        raise Denied("SVG use must reference a pinned asset or local fragment")
        if tag.name in ("image", "feimage"):
            for key in ("href", "xlink:href"):
                if tag.get(key):
                    tag[key] = convert(tag[key])
        if tag.get("style"):
            tag["style"] = css_images(tag["style"])
        if tag.name == "style" and tag.string:
            tag.string.replace_with(css_images(str(tag.string)))
    rendered = str(soup)
    validate_html(rendered, site_url, external_origins)
    return rendered


def render_pdf(doc, format_name, args, resources):
    import frappe
    from frappe.utils import get_url
    from frappe.utils.pdf import get_pdf
    from frappe.utils.print_format import print_language, validate_print_permission

    validate_print_permission(doc)
    with print_language(args.get("language")):
        rendered = frappe.get_print(doc.doctype, doc.name, format_name, doc=doc, as_pdf=False,
            no_letterhead=args.get("no_letterhead", 0), pdf_generator="wkhtmltopdf")
        rendered = inline_images(rendered, get_url(allow_header_override=False), resources)
        # Frappe get_pdf explicitly disables JavaScript and local-file access.
        pdf = get_pdf(rendered)
    frappe.local.response.filename = doc.name.replace(" ", "-").replace("/", "-") + ".pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "pdf"
