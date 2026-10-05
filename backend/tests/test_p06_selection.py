from personal_os import llm


def test_selection_toolbar_defaults_on():
    # PUT /settings rejects a value whose type differs from the default, so the default must be a bool.
    assert llm.DEFAULT_SETTINGS["selectionToolbar"] is True
