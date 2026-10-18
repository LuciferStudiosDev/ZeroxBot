import os
import aiohttp

class PteroAPIError(Exception):
    pass

class PteroClient:
    def __init__(self, panel_url: str, api_key: str, kind: str):
        self.panel_url = panel_url.rstrip("/")
        self.api_key = api_key
        self.kind = kind
        self.session = None
        self.api_root = "/api/client" if kind == "client" else "/api/application"

    async def start(self):
        if not self.api_key:
            return
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "Application/vnd.pterodactyl.v1+json",
                "Content-Type": "application/json",
                "User-Agent": "ZERØX-Ptero-Manager/1.0"
            }
        )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def request(self, method, path, payload=None, params=None):
        if not self.session:
            raise PteroAPIError(f"{self.kind.title()} API is not configured. Add its key to .env.")
        # Internal callers use fixed endpoint paths, never a user-provided URL.
        url = f"{self.panel_url}{self.api_root}/{path.lstrip('/')}"
        try:
            async with self.session.request(method, url, json=payload, params=params) as resp:
                if resp.status == 204:
                    return {}
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    data = {}
                if resp.status >= 400:
                    if resp.status in (401, 403):
                        raise PteroAPIError(f"{self.kind.title()} API denied this action (HTTP {resp.status}); check key permissions.")
                    errors = data.get("errors", []) if isinstance(data, dict) else []
                    detail = errors[0].get("detail") if errors and isinstance(errors[0], dict) else None
                    raise PteroAPIError(str(detail or f"Pterodactyl API returned HTTP {resp.status}")[:250])
                return data if isinstance(data, dict) else {}
        except aiohttp.ClientError:
            raise PteroAPIError("Could not connect to Pterodactyl. Check the panel URL and network.") from None
        except TimeoutError:
            raise PteroAPIError("Pterodactyl request timed out.") from None

    async def pages(self, path, params=None):
        params = dict(params or {})
        params.setdefault("per_page", 100)
        result = []
        page = 1
        while page <= 10:
            params["page"] = page
            data = await self.request("GET", path, params=params)
            result.extend(data.get("data", []))
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", page) or not data.get("data"):
                break
            page += 1
        return result

    async def list_servers(self):
        return [x.get("attributes", {}) for x in await self.pages("servers")]

    async def find_server(self, identifier):
        identifier = identifier.strip().casefold()
        if not identifier or len(identifier) > 100:
            raise PteroAPIError("Enter a valid server identifier.")
        for server in await self.list_servers():
            attrs = server.get("attributes", server)
            candidates = {
                str(attrs.get("identifier", "")).casefold(),
                str(attrs.get("uuid", "")).casefold(),
                str(attrs.get("id", "")).casefold(),
                str(attrs.get("name", "")).casefold()
            }
            if identifier in candidates:
                return attrs
        raise PteroAPIError("Server not found in the accessible server list. Use /list to check its identifier.")

    async def resources(self, identifier):
        return (await self.request("GET", f"servers/{identifier}/resources")).get("attributes", {})

    async def power(self, identifier, signal):
        if signal not in {"start", "stop", "restart", "kill"}:
            raise PteroAPIError("Invalid power action.")
        await self.request("POST", f"servers/{identifier}/power", {"signal": signal})

    async def send_command(self, identifier, command):
        if len(command) > 500:
            raise PteroAPIError("Command is too long (maximum 500 characters).")
        await self.request("POST", f"servers/{identifier}/command", {"command": command})

    async def rename_client(self, identifier, name):
        await self.request("POST", f"servers/{identifier}/settings/rename", {"name": name[:100]})

    async def reinstall(self, identifier):
        await self.request("POST", f"servers/{identifier}/settings/reinstall")

    async def app_list_nodes(self):
        return [x.get("attributes", {}) for x in await self.pages("nodes")]

    async def app_list_users(self):
        return [x.get("attributes", {}) for x in await self.pages("users")]

    async def app_create_user(self, email, username, first_name, last_name, password):
        return (await self.request("POST", "users", {
            "email": email, "username": username, "first_name": first_name,
            "last_name": last_name, "password": password
        })).get("attributes", {})

    async def app_delete_server(self, server_id, force=False):
        await self.request("DELETE", f"servers/{int(server_id)}" + ("/force" if force else ""))

    async def app_suspend_server(self, server_id):
        await self.request("POST", f"servers/{int(server_id)}/suspend")

    async def app_unsuspend_server(self, server_id):
        await self.request("POST", f"servers/{int(server_id)}/unsuspend")

    async def app_list_eggs(self, nest_id):
        return [x.get("attributes", {}) for x in await self.pages(f"nests/{int(nest_id)}/eggs")]

    async def app_create_server(self, payload):
        return (await self.request("POST", "servers", payload)).get("attributes", {})
