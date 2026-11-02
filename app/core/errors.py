"""Domain errors shared by CLI, API and MCP boundaries."""


class PlatformError(Exception):
    pass


class Conflict(PlatformError):
    pass


class Forbidden(PlatformError):
    pass


class Invalid(PlatformError):
    pass


class NotFound(PlatformError):
    pass
