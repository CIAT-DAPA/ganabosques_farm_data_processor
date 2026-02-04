import pytest
from main import main
from ganabosques_orm.enums.farmsource import FarmSource

@pytest.fixture
def dummy_steps(monkeypatch):
    monkeypatch.setattr("main.get_data_sagari", lambda *args, **kwargs: print("✅ get_data_sagari"))
    monkeypatch.setattr("main.quality_control_coordinates", lambda *args, **kwargs: print("✅ quality_control_coordinates"))
    monkeypatch.setattr("main.buffer", lambda *args, **kwargs: print("✅ buffer"))
    monkeypatch.setattr("main.save_farm", lambda *args, **kwargs: print("✅ save_farm"))

def test_pipeline_all_steps(dummy_steps, capsys):
    main(FarmSource.SAGARI)
    captured = capsys.readouterr()
    assert "✅ get_data_sagari" in captured.out
    assert "✅ quality_control_coordinates" in captured.out
    assert "✅ buffer" in captured.out
    assert "✅ save_farm" in captured.out

def test_pipeline_only_steps_1_and_3(dummy_steps, capsys):
    main(FarmSource.SAGARI, selected_steps=[1, 3])
    captured = capsys.readouterr()
    assert "✅ get_data_sagari" in captured.out
    assert "✅ buffer" in captured.out
    assert "✅ quality_control_coordinates" not in captured.out
    assert "✅ save_farm" not in captured.out

def test_pipeline_from_step_2(dummy_steps, capsys):
    main(FarmSource.SAGARI, selected_steps=[2, 3, 4])
    captured = capsys.readouterr()
    assert "✅ get_data_sagari" not in captured.out
    assert "✅ quality_control_coordinates" in captured.out
    assert "✅ buffer" in captured.out
    assert "✅ save_farm" in captured.out

def test_pipeline_error_handling(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise Exception("💥 fallo en get_data_sagari")
    monkeypatch.setattr("main.get_data_sagari", fail)
    main(FarmSource.SAGARI, selected_steps=[1])
    captured = capsys.readouterr()
    assert "💥 fallo en get_data_sagari" in captured.out
    assert "Error general en el proceso" in captured.out