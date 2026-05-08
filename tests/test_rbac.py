from mesa_memory.security.rbac import AccessControl, sanitize_cmb_content


def test_access_control_permissions():
    ac = AccessControl()
    ac.grant_access("agent_1", "session_A", "WRITE")
    assert ac.check_access("agent_1", "session_A", "WRITE") is True
    assert ac.check_access("agent_1", "session_A", "READ") is True

    ac.grant_access("agent_2", "session_B", "READ")
    assert ac.check_access("agent_2", "session_B", "READ") is True
    assert ac.check_access("agent_2", "session_B", "WRITE") is False


def test_access_control_unauthorized():
    ac = AccessControl()
    assert ac.check_access("unknown_agent", "session_A", "READ") is False
    ac.grant_access("agent_1", "session_A", "READ")
    assert ac.check_access("agent_1", "unknown_session", "READ") is False


def test_sanitize_cmb_content():
    result = sanitize_cmb_content("Hello \x00 <script>alert(1)</script>   World  ")
    assert result == "Hello World"
