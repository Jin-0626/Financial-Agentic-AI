"""Memory belongs to an organization and a user, never a user alone."""
def user_namespace(rt):
    context = getattr(rt, "context", None)
    org_id = getattr(context, "org_id", "default-org")
    user_id = getattr(context, "user_id", "local-user")
    if getattr(rt, "server_info", None) and rt.server_info.user:
        user_id = rt.server_info.user.identity
    return ("memories", org_id, user_id)
