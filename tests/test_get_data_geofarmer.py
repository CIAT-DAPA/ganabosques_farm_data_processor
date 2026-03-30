import json

from get_data import get_data_geofarmer as gdg


class DummyResp:
    def __init__(self, payload=None, ok=True, status_code=200):
        self._payload = payload or {}
        self.ok = ok
        self.status_code = status_code

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_select_channels_by_value_chain(monkeypatch):
    monkeypatch.setattr(
        gdg,
        "CHANNELS",
        {
            "A": {"VALUE_CHAIN": "livestock", "CLIENT_ID": "1", "CLIENT_SECRET": "x"},
            "B": {"VALUE_CHAIN": "cacao", "CLIENT_ID": "2", "CLIENT_SECRET": "y"},
        },
    )

    all_channels = gdg._select_channels_by_value_chain(None)
    assert set(all_channels.keys()) == {"A", "B"}

    livestock = gdg._select_channels_by_value_chain("livestock")
    assert set(livestock.keys()) == {"A"}


def test_get_farms_paginated(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, params=None):
        calls["n"] += 1
        page = params["page"]
        if page == 1:
            return DummyResp(
                {
                    "current_page": 1,
                    "last_page": 2,
                    "total": 3,
                    "next_page_url": "next",
                    "data": [{"id": "f1"}, {"id": "f2"}],
                }
            )
        return DummyResp(
            {
                "current_page": 2,
                "last_page": 2,
                "total": 3,
                "next_page_url": None,
                "data": [{"id": "f3"}],
            }
        )

    monkeypatch.setattr(gdg.requests, "get", fake_get)
    monkeypatch.setattr(gdg.time, "sleep", lambda *_args, **_kwargs: None)

    farms, total = gdg.get_farms_paginated("token", per_page=2, sleep_secs=0)
    assert total == 3
    assert [f["id"] for f in farms] == ["f1", "f2", "f3"]
    assert calls["n"] == 2


def test_save_farm_as_geojson_verified(tmp_path):
    farm = {
        "id": "GF-1",
        "farm_name": "Mi Finca",
        "farm_code": "SIT-1",
        "geometry": {"coordinates": [-74.0, 5.0]},
        "farm_boundary": {
            "id": "b-1",
            "boundary_size": 10,
            "verification_status": "verified",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-74.0, 5.0], [-74.0, 5.1], [-73.9, 5.1], [-73.9, 5.0], [-74.0, 5.0]]],
            },
        },
    }

    ok, out = gdg.save_farm_as_geojson(farm, str(tmp_path))
    assert ok is True
    saved = json.loads((tmp_path / "FARM_ID_GF-1.geojson").read_text(encoding="utf-8"))
    assert saved["features"][0]["properties"]["farm_id"] == "GF-1"


def test_save_farm_as_geojson_not_verified(tmp_path):
    farm = {
        "id": "GF-2",
        "farm_boundary": {
            "verification_status": "pending",
            "geometry": {"type": "Point", "coordinates": [-74.0, 5.0]},
        },
    }
    ok, msg = gdg.save_farm_as_geojson(farm, str(tmp_path))
    assert ok is None
    assert msg == "no_verificada"


def test_save_farm_as_geojson_without_boundary(tmp_path):
    farm = {"id": "GF-3", "farm_name": "SinBoundary"}
    ok, msg = gdg.save_farm_as_geojson(farm, str(tmp_path))
    assert ok is False
    assert "Sin farm_boundary" in msg


def test_save_farm_as_geojson_boundary_without_geometry(tmp_path):
    farm = {
        "id": "GF-4",
        "farm_boundary": {"verification_status": "verified"},
    }
    ok, msg = gdg.save_farm_as_geojson(farm, str(tmp_path))
    assert ok is False
    assert "sin geometría" in msg


def test_main_raises_when_no_channels(monkeypatch, tmp_path):
    monkeypatch.setattr(gdg, "_select_channels_by_value_chain", lambda *_args, **_kwargs: {})
    try:
        gdg.main(output_dir=str(tmp_path), value_chain="cacao")
        assert False, "Debió lanzar ValueError"
    except ValueError as e:
        assert "No hay canales GeoFarmer" in str(e)


def test_get_token_success(monkeypatch):
    monkeypatch.setattr(
        gdg.requests,
        "post",
        lambda *_args, **_kwargs: DummyResp(payload={"access_token": "tok"}),
    )
    assert gdg.get_token("id", "secret") == "tok"


def test_process_company_auth_error(monkeypatch, tmp_path):
    def fail_token(*_args, **_kwargs):
        raise RuntimeError("auth fail")

    monkeypatch.setattr(gdg, "get_token", fail_token)
    stats = gdg.process_company("Comp", {"CLIENT_ID": "1", "CLIENT_SECRET": "2"}, str(tmp_path), str(tmp_path / "err"))
    assert stats["errores"] == 1
    assert "Error autenticación" in stats["error_detalle"][0]["error"]


def test_process_company_fetch_error(monkeypatch, tmp_path):
    monkeypatch.setattr(gdg, "get_token", lambda *_args, **_kwargs: "tok")

    def fail_fetch(*_args, **_kwargs):
        raise RuntimeError("fetch fail")

    monkeypatch.setattr(gdg, "get_farms_paginated", fail_fetch)
    stats = gdg.process_company("Comp", {"CLIENT_ID": "1", "CLIENT_SECRET": "2"}, str(tmp_path), str(tmp_path / "err"))
    assert stats["errores"] == 1
    assert "Error obteniendo fincas" in stats["error_detalle"][0]["error"]


def test_process_company_counts_and_error_file(monkeypatch, tmp_path):
    monkeypatch.setattr(gdg, "get_token", lambda *_args, **_kwargs: "tok")
    farms = [
        {"id": "1", "farm_name": "A", "farm_code": "C1"},
        {"id": "2", "farm_name": "B", "farm_code": "C2"},
        {"id": "3", "farm_name": "C", "farm_code": "C3"},
        {"id": "4", "farm_name": "D", "farm_code": "C4"},
    ]
    monkeypatch.setattr(gdg, "get_farms_paginated", lambda *_args, **_kwargs: (farms, 4))

    outcomes = iter(
        [
            (True, "ok"),
            (None, "no_verificada"),
            (False, "sin boundary"),
            RuntimeError("boom"),
        ]
    )

    def fake_save(*_args, **_kwargs):
        out = next(outcomes)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(gdg, "save_farm_as_geojson", fake_save)
    monkeypatch.setattr(gdg, "tqdm", lambda it, **_kwargs: it)

    err_dir = tmp_path / "err"
    stats = gdg.process_company("Comp", {"CLIENT_ID": "1", "CLIENT_SECRET": "2"}, str(tmp_path), str(err_dir))

    assert stats["guardadas"] == 1
    assert stats["no_verificadas"] == 1
    assert stats["omitidas"] == 1
    assert stats["errores"] == 1
    assert "errors_csv" in stats


def test_main_success_prints_summary_and_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(
        gdg,
        "_select_channels_by_value_chain",
        lambda *_args, **_kwargs: {"A": {"CLIENT_ID": "1", "CLIENT_SECRET": "x"}, "B": {"CLIENT_ID": "2", "CLIENT_SECRET": "y"}},
    )
    monkeypatch.setattr(
        gdg,
        "process_company",
        lambda company, *_args, **_kwargs: {
            "empresa": company,
            "total_api": 2,
            "guardadas": 1,
            "no_verificadas": 1,
            "omitidas": 0,
            "errores": 0,
            "error_detalle": [],
        },
    )

    logs = []
    monkeypatch.setattr(gdg.logger, "info", lambda msg: logs.append(msg))

    gdg.main(output_dir=str(tmp_path), value_chain="livestock")
    assert len(logs) == 2
