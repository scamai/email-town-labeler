"""A fresh desktop app classifies with the published on-device model and says when it is downloading it."""

import threading

from email_town_labeler.desktop import manager as m
from email_town_labeler.models import catalog
from tests.test_desktop_manager import BatchReader, CountingModel, _record, _workbench


def test_fresh_install_uses_the_published_model_and_a_config_file_wins(tmp_path):
    workbench, *_ = _workbench(tmp_path)
    assert workbench.model_ref == m.DESKTOP_MODEL and catalog.get(m.DESKTOP_MODEL) is not None
    workbench.close()
    workbench.paths.config_file.write_text('[model]\nref = "rules"\n')
    again = m.Workbench(tmp_path / "workbench", workbench.paths, reader_factory=lambda a: None)
    assert again.model_ref == "rules"
    again.close()


def test_first_model_download_is_shown_while_it_runs_and_cleared_after(tmp_path, monkeypatch):
    workbench, readers, first, second = _workbench(tmp_path)
    workbench.model = None
    readers[first] = BatchReader([_record("u", category=None)])
    readers[second] = BatchReader([])
    loading, release, seen = threading.Event(), threading.Event(), []

    def load(ref, **kwargs):
        seen.append(ref)
        loading.set()
        release.wait(5)
        return CountingModel()

    monkeypatch.setattr(m, "load_service_model", load)
    thread = threading.Thread(target=workbench._refresh)
    thread.start()
    assert loading.wait(5)
    status = workbench.snapshot()["model_status"]
    assert status == {"state": "downloading", "size": catalog.human_size(catalog.get(m.DESKTOP_MODEL).size)}
    release.set()
    thread.join(5)
    assert seen == [m.DESKTOP_MODEL] and workbench.snapshot()["model_status"] is None
    workbench.close()


def test_an_installed_model_loads_without_a_download_notice(tmp_path, monkeypatch):
    workbench, readers, first, second = _workbench(tmp_path)
    workbench.model = None
    readers[first] = BatchReader([_record("u", category=None)])
    readers[second] = BatchReader([])
    monkeypatch.setattr(catalog, "installed", lambda entry, models_dir: tmp_path)
    statuses = []

    def load(ref, **kwargs):
        statuses.append(workbench.model_status)
        return CountingModel()

    monkeypatch.setattr(m, "load_service_model", load)
    workbench._refresh()
    assert statuses == [None]
    workbench.close()
