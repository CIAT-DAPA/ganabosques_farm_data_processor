import os

import pandas as pd

from get_data.get_data_sagari import get_data_sagari, generar_agg_sagari


def test_generar_agg_sagari_returns_expected_keys():
    df = pd.DataFrame(columns=["AFTOSA_BOVINOS_HEMBRAS_MENORES_A_3_MESES"])
    agg = generar_agg_sagari(df)
    assert isinstance(agg, dict)
    assert any(k.endswith("_BOVINOS") for k in agg.keys())


def test_get_data_sagari_creates_output_csv(tmp_path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()

    df = pd.DataFrame(
        [
            {
                "CODIGO_SIT": "SIT-1",
                "LATITUD": 5.0,
                "LONGITUD": -74.0,
                "CODIGO_RUV": "RUV-1",
                "AFTOSA_BOVINOS_HEMBRAS_MENORES_A_3_MESES": 2,
            }
        ]
    )
    xlsx = in_dir / "file1.xlsx"
    df.to_excel(xlsx, index=False)

    get_data_sagari(str(in_dir), str(out_dir))

    out_csv = out_dir / "SAGARI_completo.csv"
    assert out_csv.exists()

    out_df = pd.read_csv(out_csv)
    assert "SIT_CODE" in out_df.columns
    assert out_df.iloc[0]["SIT_CODE"] == "SIT-1"
    assert "CODIGO_RUV" in out_df.columns


def test_get_data_sagari_without_excel_files_returns(tmp_path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    (in_dir / "note.txt").write_text("hola", encoding="utf-8")

    get_data_sagari(str(in_dir), str(out_dir))

    out_csv = out_dir / "SAGARI_completo.csv"
    assert not out_csv.exists()


def test_get_data_sagari_consolidates_multiple_excel_files(tmp_path):
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()

    df1 = pd.DataFrame(
        [
            {
                "CODIGO_SIT": "SIT-1",
                "LATITUD": 5.0,
                "LONGITUD": -74.0,
                "AFTOSA_BOVINOS_HEMBRAS_MENORES_A_3_MESES": 1,
            }
        ]
    )
    df2 = pd.DataFrame(
        [
            {
                "CODIGO_SIT": "SIT-2",
                "LATITUD": 5.2,
                "LONGITUD": -74.2,
                "CODIGO_RUV": "RUV-2",
                "AFTOSA_BOVINOS_HEMBRAS_MENORES_A_3_MESES": 3,
            }
        ]
    )

    df1.to_excel(in_dir / "a.xlsx", index=False)
    df2.to_excel(in_dir / "b.xlsx", index=False)

    get_data_sagari(str(in_dir), str(out_dir))

    out_df = pd.read_csv(out_dir / "SAGARI_completo.csv")
    assert sorted(out_df["SIT_CODE"].tolist()) == ["SIT-1", "SIT-2"]
    assert "TERNEROS_MENORES_1_ANIO_BOVINOS" in out_df.columns
