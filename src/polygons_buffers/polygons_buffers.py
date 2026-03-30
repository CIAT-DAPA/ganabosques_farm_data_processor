import os
import numpy as np
import pandas as pd
import geopandas as gpd
from tqdm import tqdm
from shapely.geometry import Point, Polygon

from ganabosques_orm.enums.species import Species
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.source import Source

from config import config
from mongoengine.connection import get_db
from ganabosques_orm.collections.adm1 import Adm1

# nuevo: pyproj para buffers geodésicos
from pyproj import Geod

def buffer(path_input, path_output, source):
    # 1) Cargar CSV (primer .csv en la carpeta)
    csv_files = [f for f in os.listdir(path_input) if f.lower().endswith(".csv")]
    if not csv_files:
        print("❌ No se encontró ningún archivo CSV en:", path_input)
        return
    ruta_csv = os.path.join(path_input, csv_files[0])
    print("📄 CSV cargado:", ruta_csv)
    df = pd.read_csv(ruta_csv)

    # Validaciones mínimas de columnas
    needed_count_cols = [
        f"{UGG.TERNEROS_MENORES_1_ANIO.name}_{Species.BOVINOS.name}",
        f"{UGG.HEMBRAS_MACHOS_1_2_ANIOS.name}_{Species.BOVINOS.name}",
        f"{UGG.HEMBRAS_MENORES_2_3_ANIOS.name}_{Species.BOVINOS.name}",
        f"{UGG.MACHOS_2_3_ANIOS.name}_{Species.BOVINOS.name}",
        f"{UGG.HEMBRAS_MAYORES_3_ANIOS.name}_{Species.BOVINOS.name}",
        f"{UGG.MACHOS_MAYORES_3_ANIOS.name}_{Species.BOVINOS.name}",
        f"{UGG.TERNEROS_MENORES_1_ANIO.name}_{Species.BUFALINOS.name}",
        f"{UGG.HEMBRAS_MACHOS_1_2_ANIOS.name}_{Species.BUFALINOS.name}",
        f"{UGG.HEMBRAS_MENORES_2_3_ANIOS.name}_{Species.BUFALINOS.name}",
        f"{UGG.MACHOS_2_3_ANIOS.name}_{Species.BUFALINOS.name}",
        f"{UGG.HEMBRAS_MAYORES_3_ANIOS.name}_{Species.BUFALINOS.name}",
        f"{UGG.MACHOS_MAYORES_3_ANIOS.name}_{Species.BUFALINOS.name}",
    ]
    for c in needed_count_cols + ["LONGITUD", "LATITUD", "adm1", Source.SIT_CODE.value]:
        if c not in df.columns:
            raise ValueError(f"Columna requerida no encontrada: {c}")

    # Asegurar que coordenadas sean numéricas
    df["LONGITUD"] = pd.to_numeric(df["LONGITUD"], errors="coerce")
    df["LATITUD"]  = pd.to_numeric(df["LATITUD"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["LONGITUD", "LATITUD"])
    print(f"🧹 Registros sin coords eliminados: {before - len(df)}")

    # 2) Pesos UGG
    G1 = config["UGG_GRUPOS"]["G1"]; G2 = config["UGG_GRUPOS"]["G2"]
    G3 = config["UGG_GRUPOS"]["G3"]; G4 = config["UGG_GRUPOS"]["G4"]
    G5 = config["UGG_GRUPOS"]["G5"]; G6 = config["UGG_GRUPOS"]["G6"]

    # 3) UGG por especie
    df["BOV_UGG"] = (
        G1*df[f"{UGG.TERNEROS_MENORES_1_ANIO.name}_{Species.BOVINOS.name}"] +
        G2*df[f"{UGG.HEMBRAS_MACHOS_1_2_ANIOS.name}_{Species.BOVINOS.name}"] +
        G3*df[f"{UGG.HEMBRAS_MENORES_2_3_ANIOS.name}_{Species.BOVINOS.name}"] +
        G4*df[f"{UGG.MACHOS_2_3_ANIOS.name}_{Species.BOVINOS.name}"] +
        G5*df[f"{UGG.HEMBRAS_MAYORES_3_ANIOS.name}_{Species.BOVINOS.name}"] +
        G6*df[f"{UGG.MACHOS_MAYORES_3_ANIOS.name}_{Species.BOVINOS.name}"]
    )
    df["BUF_UGG"] = (
        G1*df[f"{UGG.TERNEROS_MENORES_1_ANIO.name}_{Species.BUFALINOS.name}"] +
        G2*df[f"{UGG.HEMBRAS_MACHOS_1_2_ANIOS.name}_{Species.BUFALINOS.name}"] +
        G3*df[f"{UGG.HEMBRAS_MENORES_2_3_ANIOS.name}_{Species.BUFALINOS.name}"] +
        G4*df[f"{UGG.MACHOS_2_3_ANIOS.name}_{Species.BUFALINOS.name}"] +
        G5*df[f"{UGG.HEMBRAS_MAYORES_3_ANIOS.name}_{Species.BUFALINOS.name}"] +
        G6*df[f"{UGG.MACHOS_MAYORES_3_ANIOS.name}_{Species.BUFALINOS.name}"]
    )

    # 4) Equivalencias UGG/ha por departamento desde Mongo (Adm1)

    # Intento 1: usando el modelo (si el campo se llama 'ugg_size' correctamente)
    try:
        docs = Adm1.objects.only("ext_id", "ugg_size")
        rows = [{"codigo_dane": str(d.ext_id), "ugg_ha": d.ugg_size} for d in docs]
        ugg = pd.DataFrame(rows)
        # Si todos NaN o vacío, forzamos fallback
        if ugg.empty or ugg["ugg_ha"].dropna().empty:
            raise RuntimeError("Sin datos válidos en 'ugg_size'.")
    except Exception:
        # Fallback: leer crudo permitiendo el campo con espacio ' ugg_size'
        db = get_db()
        raw = list(db[Adm1._get_collection_name()].find({}, {"ext_id": 1, "ugg_size": 1, "ugg_size": 1}))
        rows = []
        for doc in raw:
            val = doc.get("ugg_size", None)
            if val is None:
                val = doc.get(" ugg_size", None)  # <- campo con espacio
            rows.append({"codigo_dane": str(doc.get("ext_id")), "ugg_ha": val})
        ugg = pd.DataFrame(rows)

    # Limpiar equivalencias
    ugg = ugg.dropna(subset=["ugg_ha"])
    if ugg.empty:
        raise RuntimeError("No se encontraron equivalencias UGG/ha en Adm1 (revisa el campo ugg_size / ' ugg_size').")

    df["adm1"] = df["adm1"].astype(str)
    ugg["codigo_dane"] = ugg["codigo_dane"].astype(str)
    df = df.merge(ugg, how="left", left_on="adm1", right_on="codigo_dane")
    df["ugg_equiv_dep"] = df["ugg_ha"]

    # Mostrar equivalencias una sola vez
    print("\n📊 Equivalencias UGG/ha por departamento (una sola vez):")
    print(df[["adm1", "ugg_equiv_dep"]].drop_duplicates().sort_values("adm1").to_string(index=False))
    print("------------------------------------------------------------")

    # 5) Área equivalente y “radio” (DIÁMETRO, como definiste originalmente)
    df["hectareas"] = (df["BOV_UGG"]/df["ugg_equiv_dep"] + df["BUF_UGG"]/df["ugg_equiv_dep"])
    df["metros"]    = df["hectareas"] * 10000.0
    df["radio"]     = np.sqrt(df["metros"] / np.pi) * 2  # ← diámetro según tu código original

    # 6) Dejar un registro por SIT (el de mayor área)
    df = df.sort_values("hectareas", ascending=False)\
           .drop_duplicates(subset=Source.SIT_CODE.value, keep="first")

    # 7) GeoDataFrame: coords en grados → EPSG:4326
    gdf_4326 = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["LONGITUD"], df["LATITUD"]),
        crs="EPSG:4326"
    )

    # 8) **NO reproyectar a EPSG:3116** — trabajamos todo en 4326
    gdf = gdf_4326  # ahora todo queda en EPSG:4326

    # Limpieza de tipos/columnas
    if "ugg_ha" in gdf.columns:
        gdf = gdf.drop(columns=["ugg_ha"])
    gdf["hectareas"] = pd.to_numeric(gdf["hectareas"], errors="coerce")
    gdf["radio"]     = pd.to_numeric(gdf["radio"], errors="coerce").round().astype("Int64")

    # 9) Guardar CSV final (sin geometría ni columnas auxiliares)
    os.makedirs(path_output, exist_ok=True)
    path_csv = os.path.join(path_output, f"{source}_completa_final.csv")
    gdf.drop(columns=["geometry", "BOV_UGG", "BUF_UGG", "codigo_dane", "adm1", "ugg_equiv_dep"])\
       .to_csv(path_csv, index=False)
    print(f"✅ CSV final guardado en: {path_csv}")

    # 10) Buffers (GeoJSON por SIT) — **geodésicos** en EPSG:4326
    carpeta_buffers = os.path.join(path_output, "buffers")
    os.makedirs(carpeta_buffers, exist_ok=True)

    # Inicializar Geod (WGS84) para construir círculos geodésicos
    geod = Geod(ellps="WGS84")
    # número de segmentos para aproximar el círculo
    N_SEGMENTS = 64

    for row in tqdm(gdf.itertuples(index=False), total=len(gdf), desc="🛠️  Creando buffers"):
        # row.radio está en metros (diámetro según el código). Convertimos a radio (metros)
        try:
            diam = getattr(row, "radio")
        except Exception:
            diam = None

        if diam is None or (pd.isna(diam)):
            continue

        # asegurar número y positivo
        if not np.isfinite(diam) or diam <= 0:
            continue

        # convertir diámetro a radio en metros
        radius_m = float(diam) / 2.0

        # centro en lon/lat (pyproj.Geod usa lon, lat)
        lon0 = getattr(row, "LONGITUD")
        lat0 = getattr(row, "LATITUD")

        if pd.isna(lon0) or pd.isna(lat0):
            continue

        # generar puntos alrededor del círculo usando geod.fwd
        azs = np.linspace(0, 360, N_SEGMENTS, endpoint=False)
        coords = []
        for az in azs:
            lon2, lat2, _ = geod.fwd(lon0, lat0, float(az), radius_m)
            coords.append((lon2, lat2))
        # cerrar el anillo
        if coords[0] != coords[-1]:
            coords.append(coords[0])

        # crear polígono geodésico en lon/lat (EPSG:4326)
        try:
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception as e:
            # si falla la construcción del polígono, saltar y loguear si quieres
            continue

        # nombre limpio del archivo
        codigo = getattr(row, Source.SIT_CODE.value)
        if isinstance(codigo, (int, float)) and float(codigo).is_integer():
            codigo_str = str(int(codigo))
        else:
            codigo_str = str(codigo)

        # guardar en EPSG:4326
        gpd.GeoSeries([poly], crs="EPSG:4326").to_file(
            os.path.join(carpeta_buffers, f"{codigo_str}.geojson"),
            driver="GeoJSON"
        )

    print("✅ Buffers creados en carpeta:", carpeta_buffers)
