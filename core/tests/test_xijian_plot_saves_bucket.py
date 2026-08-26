"""P0-4 plot_saves 桶注册测试（存档逻辑在 P1 实现）。"""

from xijian_api.stubs import state


def test_plot_saves_bucket_registered():
    assert "plot_saves" in state.__dict__
    # 能写入并读出
    key = ("plot_test", 0)
    state.plot_saves[key] = {"ok": True}
    assert state.plot_saves[key]["ok"] is True
    del state.plot_saves[key]
    assert key not in state.plot_saves
