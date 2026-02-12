import os
import time
import json
import csv
import logging
import requests
from datetime import datetime

from tqdm import tqdm
from config import config

logger = logging.getLogger("get_data_geofarmer")

# === VARIABLES DESDE CONFIG ===
BASE_URL = config["GEOFARMER_BASE_URL"]
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

    return farms, total


def save_farm_as_geojson(farm, company_dir):
    """
    Guarda el farm_boundary verificado como GeoJSON.
    Solo guarda fincas cuyo farm_boundary.verification_status == 'verified'.
    Usa farm['id'] como nombre de archivo para evitar colisiones.
    Retorna:
      (True, filepath)            → guardada exitosamente
      (False, mensaje_error)      → omitida (sin boundary / sin geometría)
      (None, 'no_verificada')     → boundary existe pero no está verificado
    """
    boundary = farm.get("farm_boundary")

    if farm.get("manual_geometry"):
        coordenadas = farm["manual_geometry"].get("coordinates")
    elif farm.get("geometry"):
        coordenadas = farm["geometry"].get("coordinates")
    else:
        coordenadas = None

    # Sin boundary
    if not boundary or not isinstance(boundary, dict):
        return False, f"Sin farm_boundary (farm_id={farm.get('id')}, nombre={farm.get('farm_name')})"

    # Filtrar por verification_status
    status = (boundary.get("verification_status") or "").lower().strip()
    if status != "verified":
        return None, "no_verificada"

    # Sin geometría en el boundary
    geom = boundary.get("geometry")
    if not geom:
        return False, f"farm_boundary sin geometría (farm_id={farm.get('id')}, nombre={farm.get('farm_name')})"

    # Construir GeoJSON con el boundary como Feature
    feature = {
        "type": "Feature",
        "geometry": geom,
        "properties": {
            "centroid": coordenadas,
            "type": "farm_boundary",
            "farm_id": farm["id"],
            "farm_name": farm.get("farm_name"),
            "farm_code": farm.get("farm_code"),
            "contact_name": farm.get("contact_name"),
            "address": farm.get("address"),
            "boundary_id": boundary.get("id"),
            "boundary_size": boundary.get("boundary_size"),
            "verification_status": boundary.get("verification_status")
        }
    }

    geojson = {"type": "FeatureCollection", "features": [feature]}

    os.makedirs(company_dir, exist_ok=True)
    farm_id = str(farm["id"])
    filepath = os.path.join(company_dir, f"FARM_ID_{farm_id}.geojson")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    return True, filepath


def process_company(name, creds, output_dir, errors_dir):
    """
    Procesa todas las fincas de una empresa con barra de progreso.
    Guarda errores en CSV. Retorna dict con estadísticas.
    """
    stats = {
        "empresa": name, "total_api": 0,
        "guardadas": 0, "no_verificadas": 0, "omitidas": 0, "errores": 0,
        "error_detalle": []
    }

    try:
        token = get_token(creds["CLIENT_ID"], creds["CLIENT_SECRET"])
    except Exception as e:
        stats["error_detalle"].append({"farm_id": "N/A", "farm_name": "N/A", "error": f"Error autenticación: {e}"})
        stats["errores"] = 1
        return stats

    try:
        farms, total_api = get_farms_paginated(token, per_page=200)
        stats["total_api"] = total_api or len(farms)
    except Exception as e:
        stats["error_detalle"].append({"farm_id": "N/A", "farm_name": "N/A", "error": f"Error obteniendo fincas: {e}"})
        stats["errores"] = 1
        return stats

    company_dir = os.path.join(output_dir, name)

    for farm in tqdm(farms, desc=f"  📥 {name}", unit="finca", leave=True):
        try:
            ok, msg = save_farm_as_geojson(farm, company_dir)
            if ok is True:
                stats["guardadas"] += 1
            elif ok is None:
                # Boundary no verificado → no es error, solo se omite silenciosamente
                stats["no_verificadas"] += 1
                stats["error_detalle"].append({
                    "farm_id": farm.get("id", "?"),
                    "farm_name": farm.get("farm_name", "?"),
                    "farm_code": farm.get("farm_code", "?"),
                    "error": msg
                })
            else:
                stats["omitidas"] += 1
                stats["error_detalle"].append({
                    "farm_id": farm.get("id", "?"),
                    "farm_name": farm.get("farm_name", "?"),
                    "farm_code": farm.get("farm_code", "?"),
                    "error": msg
                })
        except Exception as e:
            stats["errores"] += 1
            stats["error_detalle"].append({
                "farm_id": farm.get("id", "?"),
                "farm_name": farm.get("farm_name", "?"),
                "farm_code": farm.get("farm_code", "?"),
                "error": str(e)
            })

    # Guardar CSV de errores/omisiones si hay
    if stats["error_detalle"]:
        os.makedirs(errors_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        err_path = os.path.join(errors_dir, f"errores_{name}_{ts}.csv")
        with open(err_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["farm_id", "farm_name", "farm_code", "error"])
            writer.writeheader()
            writer.writerows(stats["error_detalle"])
        stats["errors_csv"] = err_path

    return stats


def main(output_dir: str = None, errors_dir: str = None):
    if output_dir is None:
        output_dir = config.get("GEOFARMER_OUTPUT_API", "geofarmer_output")
    if errors_dir is None:
        errors_dir = os.path.join(output_dir, "_errores")
    os.makedirs(output_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  DESCARGA DE DATOS GEOFARMER")
    print(f"  Empresas: {', '.join(CLIENTS.keys())}")
    print(f"  Salida:   {output_dir}")
    print(f"{'='*60}")

    resultados = []
    for company, creds in CLIENTS.items():
        stats = process_company(company, creds, output_dir, errors_dir)
        resultados.append(stats)

    # Resumen final
    print(f"\n{'='*60}")
    print(f"  RESUMEN DESCARGA GEOFARMER")
    print(f"{'='*60}")
    total_ok = total_noverif = total_omit = total_err = 0
    for r in resultados:
        total_ok += r["guardadas"]
        total_noverif += r["no_verificadas"]
        total_omit += r["omitidas"]
        total_err += r["errores"]
        icono = "✅" if r["errores"] == 0 and r["omitidas"] == 0 else "⚠️"
        print(f"  {icono} {r['empresa']:25s} | API: {r['total_api']:>4} | verified: {r['guardadas']:>4} | no verif: {r['no_verificadas']:>4} | sin boundary: {r['omitidas']:>3} | errores: {r['errores']:>3}")
    print(f"  {'─'*70}")
    print(f"  TOTAL: {total_ok} verified | {total_noverif} no verificadas | {total_omit} sin boundary | {total_err} errores")
    print(f"{'='*75}\n")
    for r in resultados:
        logger.info(f"Empresa: {r['empresa']}, Total API: {r['total_api']}, Guardadas: {r['guardadas']}, No verificadas: {r['no_verificadas']}, Sin boundary: {r['omitidas']}, Errores: {r['errores']}, CSV errores: {r.get('errors_csv', 'N/A')}")


if __name__ == "__main__":
    main()
