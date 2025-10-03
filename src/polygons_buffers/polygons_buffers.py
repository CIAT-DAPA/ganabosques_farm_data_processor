import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import numpy as np
import os
from tqdm import tqdm
from ganabosques_orm.enums.species import Species
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.source import Source
from config import config
from mongoengine import connect
from ganabosques_orm.collections.adm1 import Adm1

def buffer(path_input, path_output, source):
    # 1. Buscar y cargar CSV
    csv_files = [f for f in os.listdir(path_input) if f.endswith(".csv")]
    if not csv_files:
        print("❌ No se encontró ningún archivo CSV en:", path_input)
        return
    ruta_csv = os.path.join(path_input, csv_files[0])
    print("📄 CSV cargado:", ruta_csv)
    df = pd.read_csv(ruta_csv)

    # 2. Parámetros UGG
    G1 = config["UGG_GRUPOS"]["G1"]
    G2 = config["UGG_GRUPOS"]["G2"]
    G3 = config["UGG_GRUPOS"]["G3"]
    G4 = config["UGG_GRUPOS"]["G4"]
    G5 = config["UGG_GRUPOS"]["G5"]
    G6 = config["UGG_GRUPOS"]["G6"]

    # 3. Calcular UGG
    df["BOV_UGG"] = (
        G1 * df[f"{UGG.TERNEROS_MENORES_1_ANIO.name}_{Species.BOVINOS.name}"] +
        G2 * df[f"{UGG.HEMBRAS_MACHOS_1_2_ANIOS.name}_{Species.BOVINOS.name}"] +
        G3 * df[f"{UGG.HEMBRAS_MENORES_2_3_ANIOS.name}_{Species.BOVINOS.name}"] +
        G4 * df[f"{UGG.MACHOS_2_3_ANIOS.name}_{Species.BOVINOS.name}"] +
        G5 * df[f"{UGG.HEMBRAS_MAYORES_3_ANIOS.name}_{Species.BOVINOS.name}"] +
        G6 * df[f"{UGG.MACHOS_MAYORES_3_ANIOS.name}_{Species.BOVINOS.name}"]
    )

    df["BUF_UGG"] = (
        G1 * df[f"{UGG.TERNEROS_MENORES_1_ANIO.name}_{Species.BUFALINOS.name}"] +
        G2 * df[f"{UGG.HEMBRAS_MACHOS_1_2_ANIOS.name}_{Species.BUFALINOS.name}"] +
        G3 * df[f"{UGG.HEMBRAS_MENORES_2_3_ANIOS.name}_{Species.BUFALINOS.name}"] +
        G4 * df[f"{UGG.MACHOS_2_3_ANIOS.name}_{Species.BUFALINOS.name}"] +
        G5 * df[f"{UGG.HEMBRAS_MAYORES_3_ANIOS.name}_{Species.BUFALINOS.name}"] +
        G6 * df[f"{UGG.MACHOS_MAYORES_3_ANIOS.name}_{Species.BUFALINOS.name}"]
    )

    # 4. Conectar a Mongo y obtener equivalencias UGG
    connect(db=config['MONGO_DB_NAME'], host=config['MONGO_URI'])
    ugg = [{"codigo_dane": d.ext_id, "ugg_ha": d.ugg_size} for d in Adm1.objects()]
    ugg = pd.DataFrame(ugg).dropna()

    df["adm1"] = df["adm1"].astype(str)
    ugg["codigo_dane"] = ugg["codigo_dane"].astype(str)
    df = df.merge(ugg, how="left", left_on="adm1", right_on="codigo_dane")
    df["ugg_equiv_dep"] = df["ugg_ha"]

    # 5. Calcular área y diámetro (radio * 2)
    df["hectareas"] = (
        df["BOV_UGG"] / df["ugg_equiv_dep"] +
        df["BUF_UGG"] / df["ugg_equiv_dep"]
    )
    df["metros"] = df["hectareas"] * 10000
    df["radio"] = np.sqrt(df["metros"] / np.pi) * 2  # diámetro

    # 6. Eliminar duplicados
    df = df.sort_values("hectareas", ascending=False).drop_duplicates(
        subset=Source.SIT_CODE.value, keep="first"
    )

    # 7. Crear GeoDataFrame en EPSG:3857 (metros)
    geometry = gpd.points_from_xy(df["LONGITUD"], df["LATITUD"])
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326").to_crs(epsg=3857)

    if "ugg_ha" in gdf.columns:
        gdf = gdf.drop(columns=["ugg_ha"])
    gdf["hectareas"] = pd.to_numeric(gdf["hectareas"], errors="coerce")
    gdf["radio"] = pd.to_numeric(gdf["radio"], errors="coerce").round().astype("Int64")  # quitar .0

    # 8. Guardar CSV final sin geometría
    os.makedirs(path_output, exist_ok=True)
    path_csv = os.path.join(path_output, f"{source}_completa_final.csv")
    gdf.drop(columns=[
        "geometry", "BOV_UGG", "BUF_UGG", "codigo_dane", "adm1", "ugg_equiv_dep"
    ]).to_csv(path_csv, index=False)
    print(f"✅ CSV final guardado en: {path_csv}")

    # 9. Crear y guardar buffers en metros
    carpeta_buffers = os.path.join(path_output, "buffers")
    os.makedirs(carpeta_buffers, exist_ok=True)

    for row in tqdm(gdf.itertuples(index=False), total=len(gdf), desc="🛠️  Creando buffers"):
        if np.isfinite(row.radio):
            buffer_geom = row.geometry.buffer(row.radio)  # en metros
            buffer_wgs84 = gpd.GeoSeries([buffer_geom], crs="EPSG:3857").to_crs(4326)

            # Limpiar nombre para evitar ".0"
            codigo = getattr(row, Source.SIT_CODE.value)
            if isinstance(codigo, (int, float)) and float(codigo).is_integer():
                codigo_str = str(int(codigo))
            else:
                codigo_str = str(codigo)

            buffer_wgs84.to_file(
                os.path.join(carpeta_buffers, f"{codigo_str}.geojson"),
                driver="GeoJSON"
            )
