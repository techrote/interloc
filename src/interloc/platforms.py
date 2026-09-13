"""Platform capability probes that never import optional native providers eagerly."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.util
import os
import sys


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    status: str
    detail: str


def probe_native_providers() -> list[ProviderStatus]:
    """Report optional providers without requiring or importing them."""

    module_name = "interloc_windows_capture"
    if os.name != "nt":
        return [ProviderStatus(module_name, "not-applicable", "Windows-only provider")]
    if importlib.util.find_spec(module_name) is None:
        return [ProviderStatus(module_name, "not-installed", "optional provider is not installed")]
    return [ProviderStatus(module_name, "available", "module is discoverable; not imported by doctor")]


def runtime_summary() -> dict[str, object]:
    return {
        "python": sys.version.split()[0],
        "implementation": sys.implementation.name,
        "platform": sys.platform,
        "providers": [asdict(item) for item in probe_native_providers()],
    }
