import os
import time
import requests
import geopandas as gpd
from shapely.geometry import shape
from config import config

# === VARIABLES DESDE CONFIG ===
BASE_URL = config["GEOFARMER_BASE_URL"]
OUTPUT_DIR = config["GEOFARMER_OUTPUT_API"]
CLIENTS = config["GEOFARMER_CLIENTS"]


# === FUNCIONES ===
def get_token(client_id, client_secret):
    """Obtiene el token OAuth2 de cada cliente"""
    url = f"{BASE_URL}/oauth/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    r = requests.post(url, data=data)
    r.raise_for_status()
    return r.json().get("access_token")


def get_farms_paginated(token, per_page=200, sleep_secs=0.2):
    """Trae todas las fincas con paginación"""
    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
    farms = []
    page = 1
    total = None
    last_page = None

    while True:
        params = {"page": page, "per_page": per_page}
        r = requests.get(f"{BASE_URL}/client/farms", headers=headers, params=params)
        r.raise_for_status()
        j = r.json()

        cur = j.get("current_page")
        last_page = j.get("last_page", last_page)
        total = j.get("total", total)
        data = j.get("data", [])
        farms.extend(data)

        if not j.get("next_page_url"):
            if last_page is not None and cur is not None and cur >= last_page:
                break
            if len(data) < per_page:
                break
        page += 1
        time.sleep(sleep_secs)

    print(f"ℹ️  total reportado por API: {total} | páginas recorridas: {page-1} | fincas recibidas: {len(farms)}")
    return farms


def save_farm_as_geojson(farm, company_dir):
    """Guarda una finca y sus parcelas como GeoJSON"""
    features = []

    # Finca principal
    if farm.get("geometry"):
        features.append({
            "type": "Feature",
            "geometry": farm["geometry"],
            "properties": {
                "type": "farm",
                "farm_id": farm["id"],
                "farm_name": farm["farm_name"],
                "contact_name": farm.get("contact_name"),
                "address": farm.get("address")
            }
        })

    # Parcelas
    for parcel in farm.get("farm_parcels", []):
        if parcel.get("geometry"):
            features.append({
                "type": "Feature",
                "geometry": parcel["geometry"],
                "properties": {
                    "type": "parcel",
                    "parcel_id": parcel["id"],
                    "parcel_name": parcel.get("parcel_name"),
                    "parcel_size_m2": parcel.get("parcel_size"),
                    "farm_id": farm["id"],
                    "farm_name": farm["farm_name"]
                }
            })

    if not features:
        print(f"⚠️ {farm['farm_name']} no tiene geometrías, se omite.")
        return

    gdf = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")

    os.makedirs(company_dir, exist_ok=True)
    safe_name = farm["farm_name"].replace(" ", "_").replace("/", "_")
    filepath = os.path.join(company_dir, f"finca_{safe_name}.geojson")
    gdf.to_file(filepath, driver="GeoJSON")
    print(f"💾 Guardado: {filepath}")


def process_company(name, creds):
    """Procesa todas las fincas de una empresa"""
    print(f"\n🔹 Procesando empresa: {name}")
    try:
        token = get_token(creds["CLIENT_ID"], creds["CLIENT_SECRET"])
        farms = get_farms_paginated(token, per_page=200)
        print(f"📦 {len(farms)} fincas obtenidas para {name}")
        company_dir = os.path.join(OUTPUT_DIR, name)
        for farm in farms:
            save_farm_as_geojson(farm, company_dir)
        print(f"✅ Empresa {name} procesada correctamente.")
    except Exception as e:
        print(f"❌ Error procesando {name}: {e}")


def main():
    for company, creds in CLIENTS.items():
        process_company(company, creds)
    print("\n🎯 Descarga completada para todas las empresas.")


if __name__ == "__main__":
    main()
