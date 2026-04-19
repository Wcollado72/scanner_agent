"""
Future application integration hooks.

These hooks are intentionally non-functional in Phase 1.
They document planned integration points for a future GUI / app shell.

Planned targets:
- Tauri desktop frontend
- Future mobile companion flows
- Device connect / disconnect events
- Friendly duplicate warnings before save operations
"""


# FUTURE FEATURE: desktop app event bridge
# def emit_ui_event(event_name: str, payload: dict) -> None:
#     """
#     Future Tauri bridge:
#     The desktop app will listen for duplicate-analysis events emitted by the core.
#     """
#     pass


# FUTURE FEATURE: pre-save duplicate warning prototype
# def warn_before_saving_to_documents(candidate_path: str) -> None:
#     """
#     Future behavior:
#     If a file being saved to Documents appears highly similar to an existing file,
#     show a friendly popup with these options:
#     1. Save with a different name
#     2. Save as original_name_V2.ext
#     3. Cancel save
#     """
#     pass


# FUTURE FEATURE: external-device watcher
# def on_storage_device_connected(device_metadata: dict) -> None:
#     """
#     Future behavior:
#     Trigger an optional safe scan when an external storage device is connected.
#     """
#     pass