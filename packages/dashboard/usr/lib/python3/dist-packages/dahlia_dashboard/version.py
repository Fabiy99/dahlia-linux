"""Version of this dashboard; tools/build-packages.sh fills it in."""

VERSION = "@VERSION@"

# Every API response carries it, so the desktop app can bind itself to the
# server version (app/lib/src/server.dart).
HEADER = "X-Dahlia-Version"


class VersionHeader:
    """ASGI middleware: adds the version header to every HTTP response."""

    def __init__(self, app, version: str):
        self.app = app
        self.value = version.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_header(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((HEADER.lower().encode(), self.value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_header)
