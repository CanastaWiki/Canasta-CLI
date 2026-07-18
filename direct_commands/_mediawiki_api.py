import http.cookiejar
import json
import secrets
import ssl
import urllib.parse
import urllib.request


class MediaWikiClient:
    def __init__(self, api_url, host_header=None):
        self.api_url = api_url
        self.host_header = host_header
        cj = http.cookiejar.CookieJar()
        handlers = [urllib.request.HTTPCookieProcessor(cj)]
        if api_url.startswith("https"):
            context = ssl._create_unverified_context()
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self.opener = urllib.request.build_opener(*handlers)

    def _request(self, params=None, files=None):
        headers = {}
        if self.host_header:
            headers["Host"] = self.host_header

        if files:
            content_type, body = self._encode_multipart(params or {}, files)
            headers["Content-Type"] = content_type
            req = urllib.request.Request(self.api_url, data=body, headers=headers)
        else:
            data = urllib.parse.urlencode(params or {}).encode("utf-8")
            req = urllib.request.Request(self.api_url, data=data, headers=headers)

        with self.opener.open(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def _encode_multipart(self, fields, files):
        boundary = secrets.token_hex(16)
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}')
            parts.append(f'Content-Disposition: form-data; name="{name}"')
            parts.append('')
            parts.append(str(value))
        for name, (filename, content, mimetype) in files.items():
            parts.append(f'--{boundary}')
            parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"')
            parts.append(f'Content-Type: {mimetype}')
            parts.append('')
            if isinstance(content, str):
                content = content.encode("utf-8")
            parts.append(content)
        parts.append(f'--{boundary}--')
        parts.append(b'')

        body = b""
        for part in parts:
            if isinstance(part, str):
                body += part.encode("utf-8") + b"\r\n"
            else:
                body += part + b"\r\n"
        
        content_type = f"multipart/form-data; boundary={boundary}"
        return content_type, body

    def login(self, username, password):
        res = self._request({"action": "query", "meta": "tokens", "type": "login", "format": "json"})
        login_token = res.get("query", {}).get("tokens", {}).get("logintoken")
        if not login_token:
            raise RuntimeError("Could not retrieve login token")

        res = self._request({
            "action": "login",
            "lgname": username,
            "lgpassword": password,
            "lgtoken": login_token,
            "format": "json"
        })
        if res.get("login", {}).get("result") != "Success":
            raise RuntimeError(f"Login failed: {res.get('login', {}).get('result')}")

    def get_csrf_token(self):
        res = self._request({"action": "query", "meta": "tokens", "type": "csrf", "format": "json"})
        csrf_token = res.get("query", {}).get("tokens", {}).get("csrftoken")
        if not csrf_token:
            raise RuntimeError("Could not retrieve CSRF token")
        return csrf_token

    def edit_page(self, title, text, summary, token):
        res = self._request({
            "action": "edit",
            "title": title,
            "text": text,
            "summary": summary,
            "token": token,
            "format": "json"
        })
        if "error" in res:
            raise RuntimeError(f"Edit failed: {res['error'].get('info')}")
        if res.get("edit", {}).get("result") != "Success":
            raise RuntimeError("Edit failed")

    def upload_file(self, filename, file_content, token):
        res = self._request(
            params={
                "action": "upload",
                "filename": filename,
                "ignorewarnings": "1",
                "token": token,
                "format": "json"
            },
            files={
                "file": (filename, file_content, "image/gif")
            }
        )
        if "error" in res:
            raise RuntimeError(f"Upload failed: {res['error'].get('info')}")
        if res.get("upload", {}).get("result") != "Success":
            raise RuntimeError("Upload failed")

    def delete_page(self, title, reason, token):
        res = self._request({
            "action": "delete",
            "title": title,
            "reason": reason,
            "token": token,
            "format": "json"
        })
        if "error" in res:
            if res["error"].get("code") in ("cantdelete", "missingtitle"):
                return
            raise RuntimeError(f"Delete failed: {res['error'].get('info')}")
