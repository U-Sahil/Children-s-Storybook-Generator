"""One small exception family. Every message is written for a normal user, never a stack trace."""


class StorybookError(Exception):
    """Base class: anything the UI may show to the user as-is."""


class ConfigError(StorybookError):
    """A key or setting is missing."""


class InputError(StorybookError):
    """The form input is invalid. `problems` lists every issue at once."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class ProviderError(StorybookError):
    """A web API (Gemini / Cloudflare) refused or failed. `status` is the HTTP code if known."""

    def __init__(self, message: str, status: int | None = None):
        self.status = status
        super().__init__(message)


class StoryError(StorybookError):
    """The story could not be written or parsed."""


class PDFError(StorybookError):
    """The PDF could not be built."""
