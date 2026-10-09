"""Deterministic, current-run provenance and direct-observation verification."""

import hashlib
import json


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def receipt(tool, call_id, arguments, payload):
    input_hash, payload_hash = digest(arguments), digest(payload)
    return {
        "id": "ev_" + digest([tool, call_id, input_hash, payload_hash])[:24],
        "tool": tool,
        "call_id": call_id,
        "input_hash": input_hash,
        "payload_hash": payload_hash,
        "method_version": "research-evidence-v1",
        "reference_format": "evidence:<id>#/field (JSON Pointer; direct values only)",
    }


def unwrap(message):
    payload = json.loads(message.content)
    metadata = (
        (message.artifact or {}).get("research_evidence")
        if isinstance(message.artifact, dict)
        else None
    )
    if metadata and isinstance(payload, dict) and payload.get("_evidence") == metadata:
        payload = payload["data"]
    if metadata:
        expected_id = (
            "ev_"
            + digest(
                [
                    metadata.get("tool"),
                    metadata.get("call_id"),
                    metadata.get("input_hash"),
                    metadata.get("payload_hash"),
                ]
            )[:24]
        )
        if (
            digest(payload) != metadata.get("payload_hash")
            or metadata.get("id") != expected_id
            or metadata.get("tool") != message.name
            or metadata.get("call_id") != message.tool_call_id
        ):
            raise ValueError("Tool evidence integrity check failed")
    return payload, metadata


def pointer(payload, path):
    if not path.startswith("/"):
        raise ValueError("Evidence field must be a JSON Pointer beginning with /")
    result = payload
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            result = (
                result[int(part)]
                if isinstance(result, list) and part.isdecimal()
                else result[part]
            )
        except (KeyError, IndexError, TypeError, ValueError):
            raise ValueError("Evidence field was not returned by the tool") from None
    return result


def urls(payload):
    if isinstance(payload, dict):
        return {
            value
            for key, value in payload.items()
            if key in {"url", "source_url", "link"} and isinstance(value, str)
        } | set().union(*(urls(value) for value in payload.values()))
    if isinstance(payload, list):
        return set().union(*(urls(value) for value in payload))
    return set()
