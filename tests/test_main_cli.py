import argparse
import os
import runpy
import types
import sys
import importlib

import pytest
import mongoengine


@pytest.fixture(autouse=True)
def _set_workspace_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path))


@pytest.fixture(autouse=True)
def _patch_mongo_connect(monkeypatch):
    monkeypatch.setattr(mongoengine, "connect", lambda **_kwargs: None)


def _run_main_with_args(monkeypatch, namespace):
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: namespace)
    return runpy.run_module("main", run_name="__main__")


def test_cli_rejects_process_and_from_step_together(monkeypatch):
    ns = argparse.Namespace(source="SAGARI", value_chain="livestock", process=[1], from_step=2)
    with pytest.raises(ValueError, match="No se puede usar --process y --from_step"):
        _run_main_with_args(monkeypatch, ns)


def test_cli_requires_value_chain_for_sagari(monkeypatch):
    ns = argparse.Namespace(source="SAGARI", value_chain=None, process=None, from_step=None)
    with pytest.raises(ValueError, match="Para SAGARI debes indicar --value_chain"):
        _run_main_with_args(monkeypatch, ns)


def test_cli_rejects_out_of_range_from_step(monkeypatch):
    ns = argparse.Namespace(source="GEOFARMER", value_chain=None, process=None, from_step=9)
    with pytest.raises(ValueError, match="from_step fuera de rango"):
        _run_main_with_args(monkeypatch, ns)


def test_cli_rejects_invalid_selected_steps(monkeypatch):
    ns = argparse.Namespace(source="GEOFARMER", value_chain=None, process=[9], from_step=None)
    with pytest.raises(ValueError, match="No se especificaron pasos válidos"):
        _run_main_with_args(monkeypatch, ns)


def test_cli_process_valid_path_runs_with_noop_dependencies(monkeypatch, tmp_path):
    ns = argparse.Namespace(source="SAGARI", value_chain="livestock", process=[1], from_step=None)
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: ns)

    import get_data
    monkeypatch.setattr(get_data, "get_data_sagari", lambda *args, **kwargs: None)

    runpy.run_module("main", run_name="__main__")


def test_cli_default_branch_runs_with_noop_geofarmer(monkeypatch, tmp_path):
    shp = tmp_path / "adm3.shp"
    shp.write_text("x", encoding="utf-8")

    ns = argparse.Namespace(source="GEOFARMER", value_chain=None, process=None, from_step=None)
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: ns)

    import tools.shapefile_utils as su
    import get_data.get_data_geofarmer as gdg
    import quality_control_coordinates.quality_control_geofarmer as qcg
    import save_farm.save_farm_geofarmer as sfg

    monkeypatch.setattr(su, "ensure_adm3_shapefile", lambda **_kwargs: str(shp))
    monkeypatch.setattr(gdg, "main", lambda **_kwargs: None)
    monkeypatch.setattr(qcg, "procesar", lambda **_kwargs: None)
    monkeypatch.setattr(sfg, "run", lambda *_args, **_kwargs: None)

    runpy.run_module("main", run_name="__main__")


def test_cli_from_step_valid_branch_runs(monkeypatch):
    ns = argparse.Namespace(source="SAGARI", value_chain="livestock", process=None, from_step=4)
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: ns)

    sfs_module = importlib.import_module("save_farm.save_farm")
    monkeypatch.setattr(sfs_module, "save_farm", lambda *_args, **_kwargs: None)

    runpy.run_module("main", run_name="__main__")
