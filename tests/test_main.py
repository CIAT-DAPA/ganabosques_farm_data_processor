import pytest
from main import main
from ganabosques_orm.enums.farmsource import FarmSource
from ganabosques_orm.enums.valuechain import ValueChain

@pytest.fixture
def dummy_steps(monkeypatch):
    monkeypatch.setattr("main.get_data_sagari", lambda *args, **kwargs: print("✅ get_data_sagari"))
    monkeypatch.setattr("main.quality_control_coordinates", lambda *args, **kwargs: print("✅ quality_control_coordinates"))
    monkeypatch.setattr("main.make_buffers", lambda *args, **kwargs: print("✅ buffer"))
    monkeypatch.setattr("main.save_farm_sagari", lambda *args, **kwargs: print("✅ save_farm"))

def test_pipeline_all_steps(dummy_steps, capsys):
    main(FarmSource.SAGARI, value_chain=ValueChain.LIVESTOCK)
    captured = capsys.readouterr()
    assert "✅ get_data_sagari" in captured.out
    assert "✅ quality_control_coordinates" in captured.out
    assert "✅ buffer" in captured.out
    assert "✅ save_farm" in captured.out

def test_pipeline_only_steps_1_and_3(dummy_steps, capsys):
    main(FarmSource.SAGARI, selected_steps=[1, 3], value_chain=ValueChain.LIVESTOCK)
    captured = capsys.readouterr()
    assert "✅ get_data_sagari" in captured.out
    assert "✅ buffer" in captured.out
    assert "✅ quality_control_coordinates" not in captured.out
    assert "✅ save_farm" not in captured.out

def test_pipeline_from_step_2(dummy_steps, capsys):
    main(FarmSource.SAGARI, selected_steps=[2, 3, 4], value_chain=ValueChain.LIVESTOCK)
    captured = capsys.readouterr()
    assert "✅ get_data_sagari" not in captured.out
    assert "✅ quality_control_coordinates" in captured.out
    assert "✅ buffer" in captured.out
    assert "✅ save_farm" in captured.out

def test_pipeline_error_handling(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise Exception("💥 fallo en get_data_sagari")
    monkeypatch.setattr("main.get_data_sagari", fail)
    with pytest.raises(Exception, match="💥 fallo en get_data_sagari"):
        main(FarmSource.SAGARI, selected_steps=[1], value_chain=ValueChain.LIVESTOCK)
    captured = capsys.readouterr()
    assert "💥 fallo en get_data_sagari" in captured.out
    assert "Error general en el proceso" in captured.out


def test_main_invalid_steps_sagari_raises():
    with pytest.raises(ValueError, match="Pasos no válidos para SAGARI"):
        main(FarmSource.SAGARI, selected_steps=[9], value_chain=ValueChain.LIVESTOCK)


def test_main_invalid_steps_geofarmer_raises():
    with pytest.raises(ValueError, match="Pasos no válidos para GEOFARMER"):
        main(FarmSource.GEOFARMER, selected_steps=[4], value_chain=ValueChain.CACAO)


def test_geofarmer_pipeline_calls_three_steps(monkeypatch):
    called = {"fetch": 0, "qc": 0, "save": 0}

    monkeypatch.setattr("main.ensure_adm3_shapefile", lambda **_kwargs: "adm3.shp")
    monkeypatch.setattr("main.fetch_geofarmer_api.main", lambda **_kwargs: called.__setitem__("fetch", called["fetch"] + 1))
    monkeypatch.setattr("main.qc_geofarmer", lambda **_kwargs: called.__setitem__("qc", called["qc"] + 1))
    monkeypatch.setattr("main.save_geofarmer", lambda *args, **_kwargs: called.__setitem__("save", called["save"] + 1))

    main(FarmSource.GEOFARMER, value_chain=ValueChain.CACAO)
    assert called == {"fetch": 1, "qc": 1, "save": 1}


def test_geofarmer_fallback_adm3_from_env(monkeypatch):
    monkeypatch.setattr("main.ensure_adm3_shapefile", lambda **_kwargs: None)
    monkeypatch.setitem(__import__("main").config, "ADM3_SHP_PATH", "fallback.shp")
    monkeypatch.setattr("main.os.path.isfile", lambda p: p == "fallback.shp")

    captured = {"adm3": None}
    monkeypatch.setattr("main.fetch_geofarmer_api.main", lambda **_kwargs: None)
    monkeypatch.setattr("main.qc_geofarmer", lambda **kwargs: captured.__setitem__("adm3", kwargs.get("adm3_shp")))
    monkeypatch.setattr("main.save_geofarmer", lambda *_args, **_kwargs: None)

    main(FarmSource.GEOFARMER, selected_steps=[2], value_chain=ValueChain.LIVESTOCK)
    assert captured["adm3"] == "fallback.shp"


def test_geofarmer_fallback_adm3_missing_raises(monkeypatch):
    monkeypatch.setattr("main.ensure_adm3_shapefile", lambda **_kwargs: None)
    monkeypatch.setitem(__import__("main").config, "ADM3_SHP_PATH", "missing.shp")
    monkeypatch.setattr("main.os.path.isfile", lambda _p: False)

    with pytest.raises(FileNotFoundError, match="No se pudo obtener el shapefile ADM3"):
        main(FarmSource.GEOFARMER, selected_steps=[2], value_chain=ValueChain.LIVESTOCK)


def test_main_unsupported_source_raises():
    class DummySource:
        value = "DUMMY"

    with pytest.raises(ValueError, match="Fuente no soportada"):
        main(DummySource(), value_chain=ValueChain.LIVESTOCK)