#!/usr/bin/env python3
"""wiki-check command — verify MediaWiki instances are accessible."""

import http.cookiejar
import json
import os
import secrets
import ssl
import sys
import urllib.parse
import urllib.request
from . import _helpers
from ._helpers import register


_PROTOCOLS = ("https", "http")
_LOCAL_DOMAINS = {"localhost", "127.0.0.1"}


def _build_urls(wiki_url):
    base = wiki_url.rstrip("/")
    suffix = "/w/api.php?action=query&meta=siteinfo&format=json"
    if base.startswith("http://") or base.startswith("https://"):
        return [base + suffix]

    parsed = urllib.parse.urlsplit("http://" + base)
    host = parsed.netloc
    path = parsed.path.rstrip("/")
    if path:
        host = host + path

    return [f"{protocol}://{host}{suffix}" for protocol in _PROTOCOLS]


def _is_mediawiki_response(body):
    if not body:
        return False
    try:
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="ignore")
        data = json.loads(body)
        if "query" in data or "batchcomplete" in data:
            return True
        if "error" in data and isinstance(data["error"], dict) and data["error"].get("code") == "readapidenied":
            return True
    except Exception:
        pass
    return False


def _localhost_probe(url, instance_path):
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme
    domain = parsed.netloc
    url_path = parsed.path or "/w/api.php"

    bare_hostname = domain.split(":")[0]

    if bare_hostname in _LOCAL_DOMAINS and ":" in domain:
        req = urllib.request.Request(url)
        context = ssl._create_unverified_context() if scheme == "https" else None
        try:
            with urllib.request.urlopen(req, timeout=15, context=context) as resp:
                ok = _is_mediawiki_response(resp.read())
                return ok, url.split("?")[0], None
        except Exception:
            return False, None, None

    env = _helpers._read_env_file(instance_path, "localhost") if instance_path else {}
    if scheme == "https":
        port = env.get("HTTPS_PORT", "")
    else:
        port = env.get("HTTP_PORT", "")

    if port:
        query_suffix = f"?{parsed.query}" if parsed.query else ""
        check_url = f"{scheme}://localhost:{port}{url_path}{query_suffix}"
        api_base = f"{scheme}://localhost:{port}{url_path}"
    else:
        check_url = url
        api_base = url.split("?")[0]

    req = urllib.request.Request(check_url)
    if port:
        req.add_header("Host", domain)

    if scheme == "https":
        context = ssl._create_unverified_context()
    else:
        context = None

    try:
        with urllib.request.urlopen(req, timeout=15, context=context) as resp:
            ok = _is_mediawiki_response(resp.read())
            return ok, api_base, (domain if port else None)
    except Exception:
        return False, None, None


def _check_url(wiki_url, host, instance_path=""):
    for url in _build_urls(wiki_url):
        if _helpers._is_localhost(host):
            ok, api_url, host_header = _localhost_probe(url, instance_path)
            if ok:
                return True, api_url, host_header
        else:
            cmd = "curl -sSLk " + _helpers._shell_quote(url)
            rc, stdout = _helpers._ssh_run(host, cmd)
            if rc == 0 and _is_mediawiki_response(stdout):
                return True, url.split("?")[0], None
    return False, None, None


def _get_admin_username(instance_id, instance, wiki_id):
    cmd = "echo 'echo User::newFromId(1)->getName();' | php maintenance/run.php eval --wiki=%s" % _helpers._shell_quote(wiki_id)
    rc, stdout = _helpers._exec_in_container(instance_id, instance, cmd)
    if rc == 0 and stdout.strip():
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if lines:
            username = lines[-1]
            if username and not username.startswith("Error"):
                return username

    return "WikiSysop"


def _provision_bot_password(instance_id, instance, wiki_id, username, password):
    cmd = (
        "php maintenance/run.php createBotPassword --wiki=%s --appid=canasta-cli --grants=basic,createeditmovepage,delete,uploadfile %s %s"
        % (
            _helpers._shell_quote(wiki_id),
            _helpers._shell_quote(username),
            _helpers._shell_quote(password),
        )
    )
    rc, stdout = _helpers._exec_in_container(instance_id, instance, cmd)
    return rc == 0


def _get_or_create_bot_password(instance_id, instance, wiki_id, host):
    path = instance.get("path", "")
    bot_password_file = os.path.join(path, f"admin-bot-password_{wiki_id}")
    
    # Read existing bot password
    content = _helpers._read_remote_or_local_file(bot_password_file, host)
    if content:
        content = content.strip()
        if ":" in content:
            username, password = content.split(":", 1)
            return username.strip(), password.strip()
            
    # Generate new one if not found
    username = _get_admin_username(instance_id, instance, wiki_id)
    password = secrets.token_hex(16)
    
    if _provision_bot_password(instance_id, instance, wiki_id, username, password):
        file_content = f"{username}:{password}\n"
        if _helpers._write_remote_or_local_file(bot_password_file, host, file_content):
            if _helpers._is_localhost(host):
                try:
                    os.chmod(bot_password_file, 0o600)
                except OSError:
                    pass
            else:
                _helpers._ssh_run(host, "chmod 0600 %s" % _helpers._shell_quote(bot_password_file))
            return username, password
            
    return None, None


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


def _run_write_check(api_url, host_header, username, password):
    client = MediaWikiClient(api_url, host_header)
    client.login(f"{username}@canasta-cli", password)
    token = client.get_csrf_token()
    
    temp_page = "Canasta-Wiki-Check-Temp-Page"
    temp_file = "File:Canasta-Wiki-Check-Temp-File.gif"
    
    # 1x1 pixel transparent GIF
    gif_data = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
    
    try:
        # Create
        client.edit_page(
            title=temp_page,
            text="This is a temporary page created by the Canasta CLI wiki-check write test.",
            summary="Canasta CLI write check",
            token=token
        )
        # Edit
        client.edit_page(
            title=temp_page,
            text="This is an updated temporary page created by the Canasta CLI wiki-check write test.",
            summary="Canasta CLI write check update",
            token=token
        )
        # Upload
        client.upload_file(
            filename="Canasta-Wiki-Check-Temp-File.gif",
            file_content=gif_data,
            token=token
        )
    finally:
        try:
            client.delete_page(temp_file, "Canasta CLI write check cleanup", token)
        except Exception as e:
            print(f"Warning: Failed to delete temporary file {temp_file}: {e}", file=sys.stderr)
        try:
            client.delete_page(temp_page, "Canasta CLI write check cleanup", token)
        except Exception as e:
            print(f"Warning: Failed to delete temporary page {temp_page}: {e}", file=sys.stderr)


@register("wiki_check")
def cmd_wiki_check(args):
    instance_id, instance = _helpers._resolve_instance(args)
    host = getattr(args, "host", None) or instance.get("host") or "localhost"
    path = instance.get("path", "")
    wikis = _helpers._read_wikis(path, host)
    do_write = getattr(args, "write", False)

    if not wikis:
        print(
            "Error: no wikis configured for instance '%s'" % instance_id,
            file=sys.stderr,
        )
        return 1

    print("Checking Canasta Wiki: %s" % instance_id)

    all_ok = True
    for wiki in wikis:
        wiki_id = wiki.get("id")
        wiki_url = wiki.get("url", "").strip()
        if not wiki_url:
            print("Wiki '%s' failed: missing wiki URL in wikis.yaml." % wiki_id)
            all_ok = False
            continue

        is_reachable, api_url, host_header = _check_url(wiki_url, host, instance_path=path)

        if is_reachable:
            print("Wiki '%s' is reachable at %s." % (wiki_id, wiki_url))
            if do_write:
                print("Performing write checks for '%s'..." % wiki_id)
                try:
                    username, password = _get_or_create_bot_password(instance_id, instance, wiki_id, host)
                    if not username or not password:
                        raise RuntimeError("Failed to retrieve or create bot password credentials")
                    _run_write_check(api_url, host_header, username, password)
                    print("Wiki '%s' write checks passed." % wiki_id)
                except Exception as e:
                    print("Wiki '%s' write checks failed: %s" % (wiki_id, e), file=sys.stderr)
                    all_ok = False
        else:
            print("Wiki '%s' could not be reached at %s." % (wiki_id, wiki_url))
            all_ok = False

    return 0 if all_ok else 1
