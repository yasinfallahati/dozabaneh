import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.prerequisites import Prerequisite


class FakeModel:
    def __init__(self, item_id, installed=False, error=None, gate=None):
        self.installed = installed
        self.installs = 0
        self.error = error
        self.gate = gate
        self.item = Prerequisite(
            id=item_id,
            label=f"label {item_id}",
            size_mb=100,
            is_installed=lambda: self.installed,
            install=self.install,
        )

    def install(self, on_progress):
        self.installs += 1
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        on_progress(0.5)
        self.installed = True


def make_client(tmp_path, items):
    app = create_app(
        data_dir=tmp_path,
        transcribe=lambda *args: ([], "en"),
        make_translator=lambda source, target: lambda lines: lines,
        translate_languages=["en"],
        speech_languages=["en"],
        prerequisites=items,
    )
    return TestClient(app)


def wait_finished(client):
    deadline = time.time() + 5
    while time.time() < deadline:
        state = client.get("/api/setup").json()
        if state["status"] != "running":
            return state
        time.sleep(0.01)
    raise AssertionError("install did not finish")


def by_id(state, item_id):
    return next(item for item in state["items"] if item["id"] == item_id)


def test_status_lists_what_is_installed_and_missing(tmp_path):
    present, missing = FakeModel("a", installed=True), FakeModel("b")
    with make_client(tmp_path, [present.item, missing.item]) as client:
        state = client.get("/api/setup").json()
    assert state["status"] == "missing"
    assert by_id(state, "a")["installed"] is True
    assert by_id(state, "b")["installed"] is False
    assert by_id(state, "b")["size_mb"] == 100


def test_status_is_complete_when_nothing_is_missing(tmp_path):
    with make_client(tmp_path, [FakeModel("a", installed=True).item]) as client:
        assert client.get("/api/setup").json()["status"] == "complete"


def test_button_installs_everything_missing(tmp_path):
    first, second = FakeModel("a"), FakeModel("b")
    with make_client(tmp_path, [first.item, second.item]) as client:
        assert client.post("/api/setup").status_code == 200
        state = wait_finished(client)
    assert state["status"] == "complete"
    assert (first.installs, second.installs) == (1, 1)


def test_installed_items_are_not_downloaded_again(tmp_path):
    present, missing = FakeModel("a", installed=True), FakeModel("b")
    with make_client(tmp_path, [present.item, missing.item]) as client:
        client.post("/api/setup")
        wait_finished(client)
    assert present.installs == 0


def test_second_press_during_install_does_not_start_again(tmp_path):
    gate = threading.Event()
    model = FakeModel("a", gate=gate)
    with make_client(tmp_path, [model.item]) as client:
        client.post("/api/setup")
        assert client.post("/api/setup").json()["status"] == "running"
        gate.set()
        wait_finished(client)
    assert model.installs == 1


def test_progress_is_visible_while_installing(tmp_path):
    gate = threading.Event()
    model = FakeModel("a", gate=gate)
    with make_client(tmp_path, [model.item]) as client:
        client.post("/api/setup")
        deadline = time.time() + 5
        while by_id(client.get("/api/setup").json(), "a")["state"] != "installing":
            assert time.time() < deadline
            time.sleep(0.01)
        gate.set()
        wait_finished(client)


def test_failed_download_is_reported_and_the_rest_still_install(tmp_path):
    broken = FakeModel("a", error=ConnectionError("blocked"))
    good = FakeModel("b")
    with make_client(tmp_path, [broken.item, good.item]) as client:
        client.post("/api/setup")
        state = wait_finished(client)
    assert state["status"] == "failed"
    assert by_id(state, "a")["state"] == "failed"
    assert "دانلود" in by_id(state, "a")["error"]
    assert by_id(state, "b")["installed"] is True


def test_pressing_again_after_failure_retries(tmp_path):
    model = FakeModel("a", error=ConnectionError("blocked"))
    with make_client(tmp_path, [model.item]) as client:
        client.post("/api/setup")
        wait_finished(client)
        model.error = None
        client.post("/api/setup")
        state = wait_finished(client)
    assert state["status"] == "complete"
    assert by_id(state, "a")["error"] is None


def test_item_needing_manual_step_shows_its_command_and_is_not_run(tmp_path):
    manual = Prerequisite(
        id="tool",
        label="tool",
        size_mb=None,
        is_installed=lambda: False,
        install=None,
        command="sudo apt install tool",
    )
    model = FakeModel("a")
    with make_client(tmp_path, [manual, model.item]) as client:
        client.post("/api/setup")
        state = wait_finished(client)
    assert state["status"] == "manual"
    assert by_id(state, "tool")["command"] == "sudo apt install tool"
    assert model.installs == 1


@pytest.mark.parametrize("unit, n, total, expected", [("B", 50, 200, 0.25), ("it", 1, 2, None)])
def test_only_byte_progress_is_reported(unit, n, total, expected):
    from app.prerequisites import byte_progress_bar

    seen = []
    bar_class = byte_progress_bar(seen.append)
    bar = bar_class(total=total, unit=unit, disable=True)
    bar.update(n)
    bar.close()
    assert (seen[-1] if seen else None) == expected


def test_network_transfer_counts_as_progress():
    from app.prerequisites import byte_progress_bar

    seen = []
    bar = byte_progress_bar(seen.append)(total=100, unit="B", desc="Downloading bytes")
    bar.update(50)
    bar.close()
    assert seen == [0.5]


def test_progress_never_moves_backwards_between_bars():
    from app.prerequisites import byte_progress_bar

    seen = []
    bar_class = byte_progress_bar(seen.append)
    network = bar_class(total=100, unit="B", desc="Downloading bytes")
    disk = bar_class(total=100, unit="B", desc="Reconstructing")
    network.update(80)
    disk.update(10)
    network.close()
    disk.close()
    assert seen == [0.8, 0.8]


def test_bar_with_unknown_total_reports_nothing():
    from app.prerequisites import byte_progress_bar

    seen = []
    bar = byte_progress_bar(seen.append)(total=0, unit="B", desc="Reconstructing")
    bar.update(50)
    bar.close()
    assert seen == []


def test_small_files_finishing_first_do_not_look_like_a_finished_download():
    from app.prerequisites import byte_progress_bar

    seen = []
    bar = byte_progress_bar(seen.append, min_total=100)(total=10, unit="B")
    bar.update(10)
    bar.close()
    assert seen == [0.1]
