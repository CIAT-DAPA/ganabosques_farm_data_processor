# -*- coding: utf-8 -*-
"""
Paso 3 GEOFARMER: Guardar farms y polígonos en MongoDB.

Lee GeoJSONs validados del paso 2 (ya contienen adm3_code, latitude,
longitude, farm_ha en las properties) y los persiste en MongoDB
como documentos Farm + FarmPolygons.

El paso 2 (quality_control_geofarmer) ya se encargó de:
  - Validar geometría / CRS
  - Buscar código ADM3 vía spatial join
  - Verificar / calcular centroide
  - Calcular área en hectáreas
"""
import os
import json
import logging
from datetime import datetime
from pathlib import Path

from tqdm import tqdm
import pandas as pd
from shapely.geometry import shape
from shapely.ops import unary_union

# ===== ORM =====
from ganabosques_orm.collections.farm import Farm
from ganabosques_orm.collections.farmpolygons import FarmPolygons
from ganabosques_orm.auxiliaries.extidfarm import ExtIdFarm
from ganabosques_orm.auxiliaries.log import Log
from ganabosques_orm.enums.farmsource import FarmSource
from ganabosques_orm.enums.source import Source
from ganabosques_orm.collections.adm3 import Adm3
from ganabosques_orm.enums.valuechain import ValueChain

from config import config
from tools.log_print import log_print

# ========= LOG =========
logger = logging.getLogger("save_farm_geofarmer")


def _channels_config() -> dict:
    return config.get("GEOFARMER_CHANNELS", {}) or {}


def _channel_metadata_from_name(channel_name: str) -> dict:
    """Busca metadata de canal de forma case-insensitive."""
    channels = _channels_config()
    if channel_name in channels:
        return channels[channel_name]

    cl = channel_name.strip().lower()
    for name, meta in channels.items():
        if name.strip().lower() == cl:
            return meta
    return {}


def _resolve_value_chain(value_chain_raw: str | None) -> ValueChain | None:
    if not value_chain_raw:
        return None
    v = str(value_chain_raw).strip().lower()
    try:
        return ValueChain(v)
    except Exception:
        return None


def _resolve_external_source(source_raw: str | None, value_chain: ValueChain | None) -> Source:
    """
    Resuelve Source a partir de configuración de canal.
    Acepta nombre de enum (SIT_CODE) o valor del enum.
    """
    if source_raw:
        s = str(source_raw).strip()
        try:
            return Source[s]
        except Exception:
            pass
        try:
            return Source(s)
        except Exception:
            pass

    # Fallback por cadena de valor
    if value_chain == ValueChain.CACAO:
        return Source.PRODUCER_ID
    return Source.SIT_CODE


# ========= HELPERS =========

def load_geojson(filepath: str) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("GeoJSON inválido (no es dict)")
    return data


def extract_first_properties(geojson_obj: dict) -> dict:
    """Extrae properties del primer Feature."""
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
        if features:
            return features[0].get("properties", {})
    elif geojson_obj.get("type") == "Feature":
        return geojson_obj.get("properties", {})
    return {}


def extract_channel_from_filepath(filepath: str) -> str:
    """Extrae nombre de canal desde carpeta padre del archivo .geojson."""
    return Path(filepath).parent.name.strip()


def extract_geofarmer_ids(geojson_obj: dict) -> set:
    """Extrae los IDs de GeoFarmer del GeoJSON (farm_id/FARM_ID)."""
    ids = set()
    features = []
    if geojson_obj.get("type") == "FeatureCollection":
        features = geojson_obj.get("features", [])
    elif geojson_obj.get("type") == "Feature":
        features = [geojson_obj]
    for feat in features:
        props = feat.get("properties", {})
        fid = props.get("farm_id", "") or props.get("FARM_ID", "")
        if fid:
            ids.add(str(fid))
    return ids


def normalize_external_code(raw_value) -> str:
    """
    Normaliza el código externo.

    Valores vacíos o equivalentes a nulos (None, null, nan, etc.)
    se consideran "sin código".
    """
    if raw_value is None:
        return ""
    value = str(raw_value).strip()
    if not value:
        return ""
    if value.lower() in {"none", "null", "nan", "na", "n/a", "sin dato"}:
        return ""
    return value


def extract_single_geofarmer_id(geojson_obj: dict) -> str:
    """
    Extrae un único GEOFARMER_ID del GeoJSON.
    Regla de negocio: 1 Farm == 1 geofarmer_id.
    """
    ids = extract_geofarmer_ids(geojson_obj)
    if not ids:
        raise ValueError("No se encontró farm_id/FARM_ID (GEOFARMER_ID) en properties")
    if len(ids) > 1:
        raise ValueError(
            f"GeoJSON con múltiples GEOFARMER_ID para una misma finca: {sorted(ids)}"
        )
    return next(iter(ids))


def get_adm3_doc_from_code(code: str):
    """Busca documento Adm3 por ext_id."""
    return Adm3.objects(ext_id=code).only("id", "ext_id").first()


def infer_value_chain_from_filepath(filepath: str) -> ValueChain | None:
    """
    Infiere cadena de valor por nombre de carpeta de canal.

    Espera estructura: .../02_quality_control/<canal>/FARM_ID_xxx.geojson
    """
    try:
        channel = extract_channel_from_filepath(filepath)
        meta = _channel_metadata_from_name(channel)
        return _resolve_value_chain(meta.get("VALUE_CHAIN"))
    except Exception:
        return None


def infer_external_source_from_filepath(filepath: str, value_chain: ValueChain | None) -> Source:
    """Infiere el Source del código externo según canal/config."""
    channel = extract_channel_from_filepath(filepath)
    meta = _channel_metadata_from_name(channel)
    return _resolve_external_source(meta.get("EXTERNAL_SOURCE"), value_chain)


def geometry_signature_from_geojson(geojson_obj: dict):
    """Genera una geometría unificada para comparar únicamente geometría."""
    geoms = []
    if geojson_obj.get("type") == "FeatureCollection":
        for feat in geojson_obj.get("features", []):
            g = feat.get("geometry")
            if g:
                geoms.append(shape(g))
    elif geojson_obj.get("type") == "Feature":
        g = geojson_obj.get("geometry")
        if g:
            geoms.append(shape(g))
    elif "type" in geojson_obj:
        geoms.append(shape(geojson_obj))

    if not geoms:
        return None
    return unary_union(geoms) if len(geoms) > 1 else geoms[0]


def same_geometry(existing_geojson_str: str, new_geojson_obj: dict) -> bool:
    """Compara si geometrías son equivalentes topológicamente."""
    try:
        existing_obj = json.loads(existing_geojson_str) if isinstance(existing_geojson_str, str) else existing_geojson_str
        g_old = geometry_signature_from_geojson(existing_obj)
        g_new = geometry_signature_from_geojson(new_geojson_obj)
        if g_old is None or g_new is None:
            return False
        return g_old.equals(g_new)
    except Exception:
        return False


def _farm_ext_codes(farm, source: Source) -> set:
    """Retorna conjunto de códigos ext_id del farm para un Source específico."""
    return {
        e.ext_code
        for e in getattr(farm, "ext_id", [])
        if getattr(e, "source", None) == source and getattr(e, "ext_code", None)
    }


def _append_report_row(
    report_rows: list,
    *,
    filepath: str,
    channel_name: str,
    value_chain: ValueChain | None,
    geofarmer_id: str,
    farm_code: str,
    external_source: Source | None,
    tipo: str,
    farm_action: str,
    poly_action: str,
    message: str,
    farm_id: str | None = None,
):
    report_rows.append({
        "archivo": os.path.basename(filepath),
        "canal": channel_name,
        "value_chain": value_chain.value if value_chain else "",
        "geofarmer_id": geofarmer_id,
        "farm_code": farm_code,
        "external_source": external_source.name if external_source else "",
        "farm_action": farm_action,
        "poly_action": poly_action,
        "farm_id": farm_id or "",
        "tipo": tipo,
        "detalle": message,
    })


# ========== UPSERT ==========

def upsert_one(filepath: str, errores: list, report_rows: list, stats: dict, value_chain: ValueChain = None):
    try:
        geojson_obj = load_geojson(filepath)
        props = extract_first_properties(geojson_obj)
        channel_name = extract_channel_from_filepath(filepath)
        warnings = []

        # Datos ya calculados en el paso 2 (QC)
        farm_code = normalize_external_code(props.get("farm_code", ""))
        adm3_code = str(props.get("adm3_code", "")).strip()
        latitude = props.get("latitude")
        longitude = props.get("longitude")
        farm_ha = props.get("farm_ha", 0.0)

        # Cadena de valor: usar la explícita; si no viene, inferir por carpeta/canal
        resolved_value_chain = value_chain or infer_value_chain_from_filepath(filepath)
        external_source = infer_external_source_from_filepath(filepath, resolved_value_chain)

        if not adm3_code:
            raise ValueError("adm3_code vacío en properties (¿no pasó por QC?)")
        if resolved_value_chain is None:
            raise ValueError(
                "No se pudo determinar value_chain (ni por parámetro ni por carpeta de canal)."
            )

        # Buscar Adm3 en MongoDB
        adm3_doc = get_adm3_doc_from_code(adm3_code)
        if not adm3_doc:
            raise ValueError(f"No se encontró Adm3 en BD para código '{adm3_code}'")

        geofarmer_id = extract_single_geofarmer_id(geojson_obj)

        # 1) Buscar Farm por GEOFARMER_ID (identidad principal) y por código externo
        farm_by_geofarmer = Farm.objects(
            ext_id__match={"source": Source.GEOFARMER_ID, "ext_code": geofarmer_id}
        ).first()

        farm_by_external = None
        if farm_code:
            farm_by_external = Farm.objects(
                ext_id__match={"source": external_source, "ext_code": farm_code}
            ).first()

        farm = None
        # Caso A: ya existe por geofarmer_id -> usar ese siempre
        if farm_by_geofarmer:
            farm = farm_by_geofarmer
            if farm_by_external and str(farm_by_external.id) != str(farm_by_geofarmer.id):
                warnings.append(
                    "Código externo ya existe en otro Farm, pero se prioriza match por GEOFARMER_ID."
                )
        else:
            # Caso B: no existe por geofarmer_id, pero existe por código externo
            if farm_by_external:
                existing_gf_codes = _farm_ext_codes(farm_by_external, Source.GEOFARMER_ID)
                if existing_gf_codes and geofarmer_id not in existing_gf_codes:
                    # Colisión: código externo repetido con geofarmer_id distinto -> NO actualizar ese farm
                    warnings.append(
                        "Colisión: código externo repetido con GEOFARMER_ID distinto. "
                        "Se crea un Farm nuevo para preservar unicidad por geofarmer_id."
                        f"GEOFARMER_ID existente en BD con códigos externos: {existing_gf_codes}"
                    )
                    farm = None
                else:
                    farm = farm_by_external

        if farm:
            # Farm existente → agregar ext_ids faltantes y actualizar solo si cambia algo
            existing_codes = {(e.source, e.ext_code) for e in farm.ext_id}
            changed = False

            if (Source.GEOFARMER_ID, geofarmer_id) not in existing_codes:
                farm.ext_id.append(ExtIdFarm(source=Source.GEOFARMER_ID, ext_code=geofarmer_id))
                changed = True

            if farm_code and (external_source, farm_code) not in existing_codes:
                farm.ext_id.append(ExtIdFarm(source=external_source, ext_code=farm_code))
                changed = True

            if farm.farm_source != FarmSource.GEOFARMER:
                farm.farm_source = FarmSource.GEOFARMER
                changed = True
            if farm.adm3_id != adm3_doc:
                farm.adm3_id = adm3_doc
                changed = True
            if farm.value_chain != resolved_value_chain:
                farm.value_chain = resolved_value_chain
                changed = True

            if changed:
                farm.log.updated = datetime.now()
                farm.save()
                action_farm = "actualizado"
                stats["farm_updates"] += 1
            else:
                action_farm = "sin_cambios"
                stats["farm_no_changes"] += 1
        else:
            # Farm nuevo
            ext_ids = []
            if farm_code:
                ext_ids.append(ExtIdFarm(source=external_source, ext_code=farm_code))
            ext_ids.append(ExtIdFarm(source=Source.GEOFARMER_ID, ext_code=geofarmer_id))
            log_obj = Log(enable=True, created=datetime.now(), updated=datetime.now())
            farm = Farm(
                adm3_id=adm3_doc,
                ext_id=ext_ids,
                farm_source=FarmSource.GEOFARMER,
                value_chain=resolved_value_chain,
                log=log_obj,
            )
            farm.save()
            action_farm = "creado"
            stats["farm_inserts"] += 1

        # 2) Polígonos: comparar geometría, versionar y manejar activo
        geojson_str = json.dumps(geojson_obj, ensure_ascii=False)
        active_poly = FarmPolygons.objects(farm_id=farm, log__enable=True).first()

        if active_poly:
            if same_geometry(active_poly.geojson, geojson_obj):
                action_poly = "sin_cambios"
                stats["poly_no_changes"] += 1
                stats["warnings"] += 1
                warnings.append("Geometría igual a la activa. No se actualiza FarmPolygon.")
            else:
                active_poly.log.enable = False
                active_poly.log.updated = datetime.now()
                active_poly.save()
                stats["poly_deactivated"] += 1

                new_poly = FarmPolygons(
                    farm_id=farm,
                    geojson=geojson_str,
                    latitude=latitude,
                    longitud=longitude,
                    farm_ha=farm_ha,
                    radio=None,
                    buffer_inputs=None,
                    log=Log(enable=True, created=datetime.now(), updated=datetime.now()),
                )
                new_poly.save()
                action_poly = "versionado"
                stats["poly_updates"] += 1
        else:
            new_poly = FarmPolygons(
                farm_id=farm,
                geojson=geojson_str,
                latitude=latitude,
                longitud=longitude,
                farm_ha=farm_ha,
                radio=None,
                buffer_inputs=None,
                log=Log(enable=True, created=datetime.now(), updated=datetime.now()),
            )
            new_poly.save()
            action_poly = "creado"
            stats["poly_inserts"] += 1

        tipo = "advertencia" if warnings else "info"
        detail = " | ".join(warnings) if warnings else "Procesado correctamente"
        _append_report_row(
            report_rows,
            filepath=filepath,
            channel_name=channel_name,
            value_chain=resolved_value_chain,
            geofarmer_id=geofarmer_id,
            farm_code=farm_code,
            external_source=external_source,
            tipo=tipo,
            farm_action=action_farm,
            poly_action=action_poly,
            message=detail,
            farm_id=str(farm.id),
        )

        return True, (
            f"canal={channel_name} | ext={external_source.name}:{farm_code or '-'} | "
            f"Farm {action_farm}, Polígono {action_poly}, ADM3={adm3_code}, "
            f"VC={resolved_value_chain.value}"
        )

    except Exception as e:
        error_row = {
            "archivo": os.path.basename(filepath),
            "canal": extract_channel_from_filepath(filepath),
            "value_chain": "",
            "geofarmer_id": "",
            "farm_code": "",
            "external_source": "",
            "farm_action": "error",
            "poly_action": "error",
            "farm_id": "",
            "tipo": "error",
            "detalle": str(e),
        }
        errores.append(error_row)
        report_rows.append(error_row)
        return False, f"ERROR {os.path.basename(filepath)}: {e}"


# ========== RUNNER ==========

def run(polygons_dir: str, errors_out_dir: str | None = None, value_chain: ValueChain = None):
    """
    Guarda farms GEOFARMER en MongoDB.
    Lee GeoJSONs del paso 2 (ya enriquecidos con adm3_code, lat, lon, farm_ha).
    """
    if not os.path.isdir(polygons_dir):
        raise FileNotFoundError(f"No existe la carpeta de polígonos: {polygons_dir}")

    all_files = sorted(str(p) for p in Path(polygons_dir).rglob("*.geojson"))
    if value_chain:
        files = [fp for fp in all_files if infer_value_chain_from_filepath(fp) == value_chain]
    else:
        files = all_files

    if not files:
        log_print(logger, f"⚠️ No se encontraron GeoJSONs en {polygons_dir}")
        return

    ok = 0
    errores, report_rows, logs = [], [], []

    stats = dict(
        farm_inserts=0,
        farm_updates=0,
        farm_no_changes=0,
        poly_inserts=0,
        poly_updates=0,
        poly_deactivated=0,
        poly_no_changes=0,
        warnings=0,
    )

    for fp in tqdm(files, desc="🧭 GEOFARMER → MongoDB"):
        success, msg = upsert_one(fp, errores, report_rows, stats, value_chain=value_chain)
        logs.append(msg)
        if success:
            ok += 1

    for line in logs:
        log_print(logger, line)

    log_print(logger, f"✅ Hechos: {ok} | ❌ Errores: {len(errores)}")
    log_print(logger, "—— Resumen —————————————————————————————————")
    log_print(logger, f"Farms GEOFARMER insertados       : {stats['farm_inserts']}")
    log_print(logger, f"Farms actualizados               : {stats['farm_updates']}")
    log_print(logger, f"Farms sin cambios                : {stats['farm_no_changes']}")
    log_print(logger, f"Polígonos insertados             : {stats['poly_inserts']}")
    log_print(logger, f"Polígonos actualizados           : {stats['poly_updates']}")
    log_print(logger, f"Polígonos desactivados           : {stats['poly_deactivated']}")
    log_print(logger, f"Polígonos sin cambios            : {stats['poly_no_changes']}")
    log_print(logger, f"Advertencias                     : {stats['warnings']}")

    if report_rows and errors_out_dir:
        os.makedirs(errors_out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_csv = os.path.join(errors_out_dir, f"reporte_geofarmer_save_{ts}.csv")
        pd.DataFrame(report_rows).to_csv(out_csv, index=False, encoding="utf-8")
        log_print(logger, f"📄 Archivo de reporte: {out_csv}")


if __name__ == "__main__":
    import argparse
    from mongoengine import connect

    connect(db=config['MONGO_DB_NAME'], host=config['MONGO_URI'])
    parser = argparse.ArgumentParser(description="Guardar farms GEOFARMER en MongoDB")
    parser.add_argument("--polygons-dir", required=True, help="Carpeta con GeoJSONs validados del paso 2")
    parser.add_argument("--errors-dir", default=None, help="Carpeta para errores (opcional)")
    args = parser.parse_args()
    run(args.polygons_dir, args.errors_dir)
