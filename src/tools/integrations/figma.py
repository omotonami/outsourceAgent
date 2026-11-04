"""Adapter Figma (H5) — CHỈ ĐỌC qua Figma REST. Code thuần, không LLM.

- Token: env FIGMA_TOKEN (personal access token, scope chỉ đọc `file_content:read`). Không log token.
  Redirect sang nơi khác `https://api.figma.com` → bỏ header X-Figma-Token (_TokenSafeRedirect).
- tokens_css: fontFamily qua whitelist ký tự (chữ/số/khoảng trắng/- _ . '), số (size/weight/line-height)
  phải là số — chặn chèn CSS từ nội dung file Figma (dữ liệu không tin cậy).
- HTTP qua urllib; `transport(method, url, headers, timeout) -> (status, bytes)` inject được để test offline.
- Client từ chối mọi method khác GET (không có hàm ghi: comment/variables POST... không tồn tại ở đây).
- export_tokens(): style trong file → design tokens dạng ui_system của b3_ui: {tokens, tokens_css}.
Luồng "làm 5% đầu": người làm design system + 1 màn mẫu trên Figma → export_tokens + sample_screen →
input b3_ui (`figma_tokens`, `sample_screen`) → B3 nhân ra các màn còn lại (xem docs/cos.md).
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.figma.com/v1"
API_HOST = "api.figma.com"
TOKEN_HEADER = "X-Figma-Token"
TIMEOUT_S = 30
FONT_FAMILY_MAX = 100
_KEY_RE = re.compile(r"^[A-Za-z0-9]+$")
_FONT_UNSAFE = re.compile(r"[^\w '.-]", re.UNICODE)     # ngoài whitelist → bỏ (gồm " ; { } < > \\ xuống dòng)


class FigmaError(Exception):
    pass


class _TokenSafeRedirect(urllib.request.HTTPRedirectHandler):
    """Theo redirect như urllib, nhưng KHÔNG mang token sang nơi khác https://api.figma.com."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        u = urllib.parse.urlsplit(newurl)
        if not (u.scheme == "https" and (u.hostname or "").lower() == API_HOST and u.port in (None, 443)):
            for store in (new.headers, new.unredirected_hdrs):
                for k in [k for k in store if k.lower() == TOKEN_HEADER.lower()]:
                    del store[k]
        return new


_OPENER = urllib.request.build_opener(_TokenSafeRedirect)


def urllib_transport(method, url, headers, timeout=TIMEOUT_S):
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as r:   # noqa: S310 (URL cố định https API)
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class FigmaClient:
    def __init__(self, token=None, transport=None, base=API):
        self.token = token if token is not None else os.environ.get("FIGMA_TOKEN", "")
        if not self.token:
            raise FigmaError("thiếu FIGMA_TOKEN (token chỉ đọc)")
        if not base.startswith("https://"):
            raise FigmaError("base phải là https")
        self.base, self.transport = base.rstrip("/"), transport or urllib_transport

    def _get(self, path, params=None):
        url = f"{self.base}{path}" + (("?" + urllib.parse.urlencode(params)) if params else "")
        status, body = self.transport("GET", url, {"X-Figma-Token": self.token, "Accept": "application/json"},
                                      TIMEOUT_S)
        if status != 200:
            raise FigmaError(f"GET {path} → HTTP {status}")        # không kèm token/body
        try:
            return json.loads(body)
        except (ValueError, TypeError) as e:
            raise FigmaError(f"GET {path}: JSON lỗi") from e

    @staticmethod
    def _key(file_key):
        if not _KEY_RE.match(str(file_key or "")):
            raise FigmaError(f"file_key không hợp lệ: {file_key!r}")
        return file_key

    # ---- đọc ----
    def file(self, file_key, depth=None):
        return self._get(f"/files/{self._key(file_key)}", {"depth": depth} if depth else None)

    def nodes(self, file_key, ids):
        ids = [str(i) for i in ids]
        if not ids:
            return {}
        return self._get(f"/files/{self._key(file_key)}/nodes", {"ids": ",".join(ids)}).get("nodes") or {}

    def styles(self, file_key):
        """Metadata style đã publish: [{key, node_id, name, style_type, description}]."""
        meta = self._get(f"/files/{self._key(file_key)}/styles").get("meta") or {}
        return meta.get("styles") or []

    def frames(self, file_key):
        """Frame cấp 1 của từng page: [{id, name, page}] (depth=2 cho nhẹ)."""
        doc = (self.file(file_key, depth=2).get("document") or {})
        return [{"id": f.get("id"), "name": f.get("name"), "page": p.get("name")}
                for p in doc.get("children") or [] for f in p.get("children") or []
                if f.get("type") in ("FRAME", "COMPONENT", "COMPONENT_SET")]


# ---------- export design tokens → ui_system (b3_ui) ----------
def _font_family(v):
    """Tên font an toàn để đặt trong chuỗi CSS: chỉ giữ ký tự whitelist; rỗng → None."""
    s = _FONT_UNSAFE.sub("", str(v or "")).strip()[:FONT_FAMILY_MAX].strip()
    return s or None


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or "token"


def _hex(c, opacity=1.0):
    r, g, b = (round(max(0.0, min(1.0, float(c.get(k, 0)))) * 255) for k in ("r", "g", "b"))
    a = float(c.get("a", 1)) * float(opacity if opacity is not None else 1)
    return f"#{r:02X}{g:02X}{b:02X}" + ("" if a >= 0.999 else f"{round(a * 255):02X}")


def _walk(node):
    yield node
    for ch in node.get("children") or []:
        yield from _walk(ch)


def export_tokens(file_json, style_nodes=None):
    """file_json = FigmaClient.file(); style_nodes (tuỳ chọn) = FigmaClient.nodes(key, style node ids).
    Trả {tokens:{color,typography,effect}, tokens_css, source:"figma"} — khớp field tokens/tokens_css
    của specs/04_schemas/ui_system.json. Chỉ lấy style có tên (design system), bỏ màu rải rác."""
    styles = file_json.get("styles") or {}
    tokens = {"color": {}, "typography": {}, "effect": {}}
    nodes = [n for n in _walk(file_json.get("document") or {})]
    for v in (style_nodes or {}).values():
        if (doc := (v or {}).get("document")):
            nodes.extend(_walk(doc))
    for n in nodes:
        for kind, sid in (n.get("styles") or {}).items():
            st = styles.get(sid)
            if not st:
                continue
            name = _slug(st.get("name", sid))
            if kind in ("fill", "fills") and st.get("styleType") == "FILL" and name not in tokens["color"]:
                solid = next((f for f in n.get("fills") or []
                              if f.get("type") == "SOLID" and f.get("visible", True)), None)
                if solid:
                    tokens["color"][name] = _hex(solid.get("color") or {}, solid.get("opacity", 1))
            elif kind == "text" and st.get("styleType") == "TEXT" and name not in tokens["typography"]:
                s = n.get("style") or {}
                typo = {k: s[k] for k in ("fontFamily", "fontWeight", "fontSize", "lineHeightPx",
                                          "letterSpacing") if k in s}
                if "fontFamily" in typo:
                    fam = _font_family(typo["fontFamily"])
                    if fam is None:
                        del typo["fontFamily"]
                    else:
                        typo["fontFamily"] = fam
                tokens["typography"][name] = typo
            elif kind == "effect" and st.get("styleType") == "EFFECT" and name not in tokens["effect"]:
                e = next((x for x in n.get("effects") or [] if x.get("type") == "DROP_SHADOW"), None)
                o = (e or {}).get("offset") or {}
                if e and all(_is_num(x) for x in (o.get("x", 0), o.get("y", 0), e.get("radius", 0))):
                    tokens["effect"][name] = (f"{o.get('x', 0)}px {o.get('y', 0)}px {e.get('radius', 0)}px "
                                              f"{_hex(e.get('color') or {})}")
    return {"tokens": tokens, "tokens_css": tokens_css(tokens), "source": "figma"}


def tokens_css(tokens):
    lines = [":root {"]
    for k, v in sorted(tokens.get("color", {}).items()):
        lines.append(f"  --color-{k}: {v};")
    for k, t in sorted(tokens.get("typography", {}).items()):
        if (fam := _font_family(t.get("fontFamily"))):
            lines.append(f"  --font-{k}-family: \"{fam}\";")
        if _is_num(t.get("fontSize")):
            lines.append(f"  --font-{k}-size: {t['fontSize']}px;")
        if _is_num(t.get("fontWeight")):
            lines.append(f"  --font-{k}-weight: {t['fontWeight']};")
        if _is_num(t.get("lineHeightPx")):
            lines.append(f"  --font-{k}-line-height: {round(float(t['lineHeightPx']), 2)}px;")
    for k, v in sorted(tokens.get("effect", {}).items()):
        lines.append(f"  --shadow-{k}: {v};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def b3_input(client, file_key, sample_frame_id, design_spec):
    """Ghép input cho b3_ui: design system + 1 màn mẫu từ Figma (đọc), B3 nhân ra màn còn lại.
    Không ghi Figma, không ghi Blackboard (b3_ui.run ghi ui_system như cũ)."""
    f = client.file(file_key)
    ids = [s["node_id"] for s in client.styles(file_key) if s.get("node_id")]
    exported = export_tokens(f, client.nodes(file_key, ids) if ids else None)
    sample = client.nodes(file_key, [sample_frame_id]).get(str(sample_frame_id)) or {}
    doc = sample.get("document") or {}
    return {"design_spec": design_spec, "figma_tokens": exported,
            "sample_screen": {"id": doc.get("id", sample_frame_id), "name": doc.get("name"),
                              "children": [c.get("name") for c in doc.get("children") or []]}}
