def current_compositor(client_instance):
    """
    Identifies the active compositor.
    Returns 'jff' while the IPC client is connected.
    """
    if client_instance and client_instance.is_connected():
        return "jff"
    return None


def translate_ipc(ev: dict, ipc_server_instance):
    return ev if ev else None
