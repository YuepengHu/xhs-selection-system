import json

from xhs_collector import keepawake


class ShellAdb:
    def __init__(self, values):
        self.values, self.cmds = dict(values), []

    def shell(self, cmd):
        self.cmds.append(cmd)
        parts = cmd.split()
        if parts[:2] == ["settings", "get"]:
            return self.values.get((parts[2], parts[3]), "null") + "\n"
        if parts[:2] == ["settings", "put"]:
            self.values[(parts[2], parts[3])] = parts[4]
        return ""


def test_enable_saves_originals_sets_values_and_restore_puts_back(tmp_path):
    adb = ShellAdb({("system", "screen_off_timeout"): "30000", ("global", "stay_on_while_plugged_in"): "2"})
    state = tmp_path / "awake.json"
    saved = keepawake.enable(adb, state)
    assert saved == {"screen_off_timeout": "30000", "stay_on_while_plugged_in": "2"}
    assert keepawake.status(adb) == {"screen_off_timeout": "2147483647", "stay_on_while_plugged_in": "7"}
    keepawake.enable(adb, state)  # 再次开启不能把已调大的值当成原值
    assert json.loads(state.read_text()) == saved
    keepawake.restore(adb, state)
    assert keepawake.status(adb) == {"screen_off_timeout": "30000", "stay_on_while_plugged_in": "2"}
    assert not state.exists()


def test_values_already_at_target_are_not_saved(tmp_path):
    adb = ShellAdb({("system", "screen_off_timeout"): "2147483647", ("global", "stay_on_while_plugged_in"): "7"})
    assert keepawake.enable(adb, tmp_path / "a.json") == {}


def test_context_manager_restores_even_on_error(tmp_path):
    adb = ShellAdb({("system", "screen_off_timeout"): "60000", ("global", "stay_on_while_plugged_in"): "0"})
    try:
        with keepawake.keep_awake(adb, tmp_path / "a.json"):
            assert keepawake.status(adb)["screen_off_timeout"] == "2147483647"
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert keepawake.status(adb) == {"screen_off_timeout": "60000", "stay_on_while_plugged_in": "0"}
    assert "input keyevent KEYCODE_WAKEUP" in adb.cmds


def test_restore_without_state_is_noop(tmp_path):
    assert keepawake.restore(ShellAdb({}), tmp_path / "none.json") == {}
