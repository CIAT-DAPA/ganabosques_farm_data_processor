import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import numpy as np
import os

def buffer(path_input, path_equiv, path_output):
    # Cargar el CSV principal
    csv_files = [f for f in os.listdir(path_input) if f.endswith(".csv")]
    if not csv_files:
        print("❌ No se encontró ningún archivo CSV en:", path_input)
        return
    ruta_csv = os.path.join(path_input, csv_files[0])
    print("📄 CSV cargado:", ruta_csv)
    df = pd.read_csv(ruta_csv)

    # Crear columnas UGG
    df["BOV_UGG"] = (
        0.5 * df["BOV_terneros_menores_1_anio"] +
        0.7 * df["BOV_hembras_machos_1_2_anios"] +
        0.8 * df["BOV_hembras_2_3_anios"] +
        0.75 * df["BOV_machos_2_3_anios"] +
        1.0 * df["BOV_hembras_mayores_3_anios"] +
        1.25 * df["BOV_machos_mayores_3_anios"]
    )

    df["BUF_UGG"] = (
        0.5 * df["BUF_terneros_menores_1_anio"] +
        0.7 * df["BUF_hembras_machos_1_2_anios"] +
        0.8 * df["BUF_hembras_2_3_anios"] +
        0.75 * df["BUF_machos_2_3_anios"] +
        1.0 * df["BUF_hembras_mayores_3_anios"] +
        1.25 * df["BUF_machos_mayores_3_anios"]
    )

    # Cargar equivalencias
    ugg = pd.read_csv(path_equiv)
    
    # Merge con df
    df = df.merge(ugg, how="left", left_on="ID_DEPARTAMENTO", right_on="codigo_dane")

    # Crear variable ugg_equiv
    df["ugg_equiv_dep"] = df["ugg_ha"]

    # Calcular hectáreas
    df["hectareas"] = (
        df["BOV_UGG"] / df["ugg_equiv_dep"] +
        df["BUF_UGG"] / df["ugg_equiv_dep"]
    )

    # Convertir radio de hectáreas a metros
    df["radio"] = np.sqrt((df["hectareas"] * 4 * 10000) / np.pi)

    # 📌 Datos duplicados
    registros_antes = len(df)
    df = df.sort_values("hectareas", ascending=False).drop_duplicates(subset="CODIGO_SIT", keep="first")
    registros_despues = len(df)
    print(f"🧹 Duplicados eliminados en 'CODIGO_SIT': {registros_antes - registros_despues}")

    # Crear geometría y geodataframe
    geometry = [Point(xy) for xy in zip(df["LONGITUD"], df["LATITUD"])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

    # Proyectar a CRS métrico
    gdf = gdf.to_crs(epsg=3857)

    # ❌ Eliminar columna innecesaria
    if "ugg_ha" in gdf.columns:
        gdf = gdf.drop(columns=["ugg_ha"])

    # Crear carpeta para los buffers
    carpeta_buffers = os.path.join(path_output, "buffers")
    os.makedirs(path_output, exist_ok=True)

    # Crear carpeta para guardar el DataFrame
    #carpeta_df = os.path.join(path_output, "03_tmp_polygons_buffers_data_frame")
    os.makedirs(path_output, exist_ok=True)

    # ✅ Guardar tabla sin geometría
    path_csv = os.path.join(path_output, "sagari_completa_final.csv")
    gdf.drop(columns="geometry").to_csv(path_csv, index=False)
    print(f"✅ Tabla guardada en: {path_csv}")

    for idx, row in gdf.iterrows():
        try:
            if np.isfinite(row["radio"]):
                buffer_geom = row.geometry.buffer(4 * row["radio"])  # 4 veces el radio
                buffer_gdf = gpd.GeoDataFrame(index=[0], geometry=[buffer_geom], crs="EPSG:3857")
                codigo_sit = int(row["CODIGO_SIT"])  # ✅ Convertir a int para quitar el .0
                output_file = os.path.join(carpeta_buffers, f"{codigo_sit}.geojson")
                buffer_gdf.to_crs(epsg=4326).to_file(output_file, driver="GeoJSON")
        except Exception as e:
            print(f"Error en fila {idx} con CODIGO_SIT {row['CODIGO_SIT']}: {e}")

# Ejecutar función
buffer(
    path_input=r"D:\OneDrive - CGIAR\Desktop\ganabosques\farms\tmp\02_tmp_quality_control_coordinates",
    path_equiv=r"D:\OneDrive - CGIAR\Desktop\ganabosques\farms\input\UGG\equivalencias_UGG_dep.csv",
    path_output=r"D:\OneDrive - CGIAR\Desktop\ganabosques\farms\tmp"
)
