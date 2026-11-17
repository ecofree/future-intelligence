"""Compatible endpoints (DeepSeek, DashScope, OpenRouter, relays such as subrouter).

An endpoint profile is non-secret configuration: a name, an HTTPS base URL, the
wire protocol (``openai_chat`` = Chat Completions, ``anthropic_messages`` =
Anthropic Messages on a relay), the NAME of the environment variable holding its
key, and the structured-output mode the endpoint supports. Relays may silently
ignore Chat Completions ``response_format`` while honoring Anthropic
``output_config``; the protocol is therefore explicit and frozen. The full profile is frozen inside each experiment entry
(``parameters.endpoint``), so the registration manifest records exactly where a
model was called. At dispatch time the worker additionally requires the frozen
profile to equal a profile in its own local registry; a database row alone can
never redirect a credential to a new host or a different key variable.

Registry = built-in presets below, plus an optional operator JSON file named by
``FI_COMPATIBLE_PROVIDERS_FILE`` (a list of profiles). A file entry with a preset
name replaces that preset. Keys are never part of a profile.
"""
from __future__ import annotations

import json
import os
import re
from typing import Mapping
from urllib.parse import urlsplit

PROVIDER = "openai_compatible"
PROFILE_KEYS = frozenset({"name", "base_url", "protocol", "api_key_env", "response_format"})
PROTOCOLS = ("openai_chat", "anthropic_messages")
RESPONSE_FORMATS = ("json_schema", "json_object")
NAME_RE = r"[a-z0-9][a-z0-9_-]{0,39}"
ENV_RE = r"[A-Z][A-Z0-9_]{2,63}"
# Host labels plus an optional fixed path; no userinfo, port, query or fragment.
BASE_URL_RE = r"https://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?(?:/[A-Za-z0-9._~-]+)*"
MODEL_RE = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}(?:/[A-Za-z0-9][A-Za-z0-9_.:-]{0,99})?"
MAX_REGISTRY_BYTES = 65536

# Documented public endpoints. Response formats are conservative: DeepSeek and
# DashScope compatible mode document JSON-object mode; schema validation is
# always repeated locally and in PostgreSQL regardless of the mode.
PRESETS = {
    "deepseek": {"name": "deepseek", "base_url": "https://api.deepseek.com", "protocol": "openai_chat",
                 "api_key_env": "DEEPSEEK_API_KEY", "response_format": "json_object"},
    "dashscope": {"name": "dashscope", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "protocol": "openai_chat", "api_key_env": "DASHSCOPE_API_KEY", "response_format": "json_object"},
    "dashscope-intl": {"name": "dashscope-intl", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                       "protocol": "openai_chat", "api_key_env": "DASHSCOPE_API_KEY", "response_format": "json_object"},
    "openrouter": {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1", "protocol": "openai_chat",
                   "api_key_env": "OPENROUTER_API_KEY", "response_format": "json_schema"},
    "xai": {"name": "xai", "base_url": "https://api.x.ai/v1", "protocol": "openai_chat",
            "api_key_env": "XAI_API_KEY", "response_format": "json_schema"},
}
# Database and token variables must never be sent to a model endpoint.
_FORBIDDEN_ENV_PREFIXES = ("FI_",)


class EndpointError(ValueError):
    """Sanitized configuration failure; never contains a credential."""


def validate_profile(value) -> dict:
    """Return an isolated, normalized profile or raise EndpointError."""
    if not isinstance(value, dict) or set(value) != PROFILE_KEYS:
        raise EndpointError("endpoint profile must contain exactly name/base_url/protocol/api_key_env/response_format")
    if not all(isinstance(value[k], str) for k in PROFILE_KEYS):
        raise EndpointError("endpoint profile fields must be strings")
    name, url, env, fmt = value["name"], value["base_url"], value["api_key_env"], value["response_format"]
    protocol = value["protocol"]
    if protocol not in PROTOCOLS:
        raise EndpointError("unsupported protocol")
    if protocol == "anthropic_messages" and fmt != "json_schema":
        raise EndpointError("anthropic_messages requires json_schema output_config")
    if not re.fullmatch(NAME_RE, name):
        raise EndpointError("invalid endpoint name")
    if len(url) > 300 or not re.fullmatch(BASE_URL_RE, url) or ".." in url:
        raise EndpointError("base_url must be https://host[/fixed/path] without port, query or credentials")
    parts = urlsplit(url)
    if parts.username or parts.password or parts.port or parts.query or parts.fragment or "." not in (parts.hostname or ""):
        raise EndpointError("base_url must name a public DNS host")
    if not re.fullmatch(ENV_RE, env) or env.startswith(_FORBIDDEN_ENV_PREFIXES):
        raise EndpointError("api_key_env must be an uppercase variable name outside FI_*")
    if fmt not in RESPONSE_FORMATS:
        raise EndpointError("unsupported response_format")
    return {"name": name, "base_url": url, "protocol": protocol, "api_key_env": env, "response_format": fmt}


def load_registry(environ: Mapping[str, str] | None = None) -> dict[str, dict]:
    """Presets plus the optional operator file. Fails closed on any malformed entry."""
    env = os.environ if environ is None else environ
    registry = {name: validate_profile(p) for name, p in PRESETS.items()}
    path = env.get("FI_COMPATIBLE_PROVIDERS_FILE")
    if path:
        try:
            with open(path, "rb") as stream:
                raw = stream.read(MAX_REGISTRY_BYTES + 1)
        except OSError:
            raise EndpointError("endpoint registry file unreadable") from None
        if len(raw) > MAX_REGISTRY_BYTES:
            raise EndpointError("endpoint registry file too large")
        try:
            items = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise EndpointError("endpoint registry must be a JSON list") from None
        if not isinstance(items, list) or len(items) > 50:
            raise EndpointError("endpoint registry must be a JSON list of at most 50 profiles")
        seen = set()
        for item in items:
            profile = validate_profile(item)
            if profile["name"] in seen:
                raise EndpointError("duplicate endpoint name in registry")
            seen.add(profile["name"])
            registry[profile["name"]] = profile
    return registry


def registered(profile: dict, environ: Mapping[str, str] | None = None) -> dict:
    """The frozen entry profile must equal the worker's local registry entry."""
    frozen = validate_profile(profile)
    local = load_registry(environ).get(frozen["name"])
    if local != frozen:
        raise EndpointError("ENDPOINT_NOT_REGISTERED")
    return frozen


def endpoint(name: str, environ: Mapping[str, str] | None = None) -> dict:
    """Look up a profile by name, e.g. to build an entry for registration."""
    profile = load_registry(environ).get(name)
    if profile is None:
        raise EndpointError("unknown endpoint name")
    return dict(profile)


def target(profile: dict) -> tuple[str, str]:
    """(host, request path) for a validated profile."""
    p = validate_profile(profile)
    parts = urlsplit(p["base_url"])
    suffix = "/messages" if p["protocol"] == "anthropic_messages" else "/chat/completions"
    return parts.hostname, (parts.path.rstrip("/") + suffix)


def wire_kind(entry: dict) -> str:
    """Parser family for an entry: official provider name, or the compatible protocol."""
    if entry.get("provider") != PROVIDER:
        return entry.get("provider")
    return "anthropic" if entry["parameters"]["endpoint"]["protocol"] == "anthropic_messages" else PROVIDER
