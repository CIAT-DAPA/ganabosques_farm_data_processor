import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import numpy as np
import os
from ganabosques_orm.enums.species import Species
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.farmsource import FarmSource
from config import config
from ganabosques_orm.enums.source import Source

def buffer(path_input, path_equiv, path_output):
    # Cargar CSV principal
    csv_files = [f for f in os.listdir(path_input) if f.endswith(".csv")]
    if not csv_files:
        print("❌ No se encontró ningún archivo CSV en:", path_input)
        return
    ruta_csv = os.path.join(path_input, csv_files[0])
    print("📄 CSV cargado:", ruta_csv)
    df = pd.read_csv(ruta_csv)

    # Obtener columnas de asignación
    asignaciones_dict = generar_agg_sagari(df)
    asignaciones = list(asignaciones_dict.keys())
    print("\n✅ Asignaciones utilizadas:", asignaciones)

    # Pesos UGG
    G1 = config["UGG_GRUPOS"]["G1"]
    G2 = config["UGG_GRUPOS"]["G2"]
    G3 = config["UGG_GRUPOS"]["G3"]
    G4 = config["UGG_GRUPOS"]["G4"]
    G5 = config["UGG_GRUPOS"]["G5"]
    G6 = config["UGG_GRUPOS"]["G6"]

    # Cálculo de BOV_UGG y BUF_UGG
    df["BOV_UGG"] = (
        G1 * df[asignaciones[0]] + #TERNEROS_MENORES_1_ANIO_BOVINOS
        G2 * df[asignaciones[2]] + #HEMBRAS_MACHOS_1_2_ANIOS_BOVINOS
        G3 * df[asignaciones[4]] + #HEMBRAS_MENORES_2_3_ANIOS_BOVINOS
        G4 * df[asignaciones[6]] + #MACHOS_2_3_ANIOS_BOVINOS
        G5 * df[asignaciones[8]] + #HEMBRAS_MAYORES_3_ANIOS_BOVINOS
        G6 * df[asignaciones[10]]  #MACHOS_MAYORES_3_ANIOS_BOVINOS
    )

    df["BUF_UGG"] = (
        G1 * df[asignaciones[1]] + #TERNEROS_MENORES_1_ANIO_BUFALINOS
        G2 * df[asignaciones[3]] + #HEMBRAS_MACHOS_1_2_ANIOS_BUFALINOS
        G3 * df[asignaciones[5]] + #HEMBRAS_MENORES_2_3_ANIOS_BUFALINOS
        G4 * df[asignaciones[7]] + #MACHOS_2_3_ANIOS_BUFALINOS
        G5 * df[asignaciones[9]] + #HEMBRAS_MAYORES_3_ANIOS_BUFALINOS
        G6 * df[asignaciones[11]]  #MACHOS_MAYORES_3_ANIOS_BUFALINOS
    )

    print("\n🔍 BOV_UGG y BUF_UGG (primeras 5 filas):")
    print(df[["BOV_UGG", "BUF_UGG"]].head())

    # Cargar equivalencias
    ugg = pd.read_csv(path_equiv)
    df["adm1"] = df["adm1"].astype(str)
    ugg["codigo_dane"] = ugg["codigo_dane"].astype(str)

    df = df.merge(ugg, how="left", left_on="adm1", right_on="codigo_dane")
    df["ugg_equiv_dep"] = df["ugg_ha"]

    print("\n🔍 Equivalencias (primeras 5 filas):")
    print(df[["adm3", "ugg_equiv_dep"]].head())

    # Calcular hectáreas
    df["hectareas"] = (
        df["BOV_UGG"] / df["ugg_equiv_dep"] +
        df["BUF_UGG"] / df["ugg_equiv_dep"]
    )

    # Calcular radio (en metros)
    df["radio"] = np.sqrt((df["hectareas"] * 4 * 10000) / np.pi)

    print("\n🔍 hectareas y radio (primeras 5 filas):")
    print(df[["hectareas", "radio"]].head())

    # Eliminar duplicados
    registros_antes = len(df)
    df = df.sort_values("hectareas", ascending=False).drop_duplicates(subset=Source.SIT_CODE.value, keep="first")
    registros_despues = len(df)
    print(f"\n🧹 Duplicados eliminados en 'SIT_CODE': {registros_antes - registros_despues}")

    # Crear geometría y GeoDataFrame
    geometry = [Point(xy) for xy in zip(df["LONGITUD"], df["LATITUD"])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

    # Proyectar a CRS métrico
    gdf = gdf.to_crs(epsg=3857)

    # Limpiar columnas
    if "ugg_ha" in gdf.columns:
        gdf = gdf.drop(columns=["ugg_ha"])

    # Asegurar valores numéricos
    gdf["hectareas"] = pd.to_numeric(gdf["hectareas"], errors="coerce")
    gdf["radio"] = pd.to_numeric(gdf["radio"], errors="coerce")

    # Crear carpeta de salida
    os.makedirs(path_output, exist_ok=True)

    # Guardar CSV sin geometría
    path_csv = os.path.join(path_output, "sagari_completa_final.csv")
    gdf.drop(columns=["geometry", "BOV_UGG", "BUF_UGG", "codigo_dane", "departamento", "ugg_equiv_dep"]).to_csv(path_csv, index=False)
    print(f"✅ CSV final guardado en: {path_csv}")

    # Crear buffers individuales
    carpeta_buffers = os.path.join(path_output, "buffers")
    os.makedirs(carpeta_buffers, exist_ok=True)

    for idx, row in gdf.iterrows():
        try:
            if np.isfinite(row["radio"]):
                buffer_geom = row.geometry.buffer(4 * row["radio"])
                buffer_gdf = gpd.GeoDataFrame(index=[0], geometry=[buffer_geom], crs="EPSG:3857")
                sit_code = int(row[Source.SIT_CODE.value])
                output_file = os.path.join(carpeta_buffers, f"{sit_code}.geojson")
                buffer_gdf.to_crs(epsg=4326).to_file(output_file, driver="GeoJSON")
        except Exception as e:
            print(f"⚠️ Error en fila {idx} (SIT_CODE {row[Source.SIT_CODE.value]}): {e}")

def generar_agg_sagari(df):
    agg_dict = {}
    for grupo in UGG:
        for especie in Species:
            key = f"{grupo.name}_{especie.name}"
            substrings = [f"AFTOSA_{especie.name}_{suf}" for suf in config[FarmSource.SAGARI.value][grupo]]
            columnas = [col for col in df if any(substr in col for substr in substrings)]
            agg_dict[key] = columnas[0] if columnas else None  # Usar solo la primera columna válida
    return agg_dict
