# -*- coding: utf-8 -*-
import logging
import os
import argparse

from config import config
from tools.log_print import log_print
from mongoengine import connect
from ganabosques_orm.enums.farmsource import FarmSource
from ganabosques_orm.enums.valuechain import ValueChain

# ====== SAGARI ======
from get_data import get_data_sagari
from quality_control_coordinates.quality_control_coordinates import quality_control_coordinates
from polygons_buffers.polygons_buffers import buffer as make_buffers
from save_farm.save_farm import save_farm as save_farm_sagari

# ====== GEOFARMER ======
# Paso 1: descarga desde API (importa el módulo completo)
from get_data import get_data_geofarmer as fetch_geofarmer_api
# Paso 2: control de calidad + ADM3 + centroide + área
from quality_control_coordinates.quality_control_geofarmer import procesar as qc_geofarmer
# Paso 3: guardar en MongoDB
from save_farm.save_farm_geofarmer import run as save_geofarmer

# Utilidad shapefile compartido
from tools.shapefile_utils import ensure_adm3_shapefile

# ============= LOG/OUTPUT BASE =============
base_path = os.path.join(config['WORKSPACE'], "farm", "outputs")
os.makedirs(base_path, exist_ok=True)

# ============= CONEXIÓN MONGODB CENTRALIZADA =============
connect(db=config['MONGO_DB_NAME'], host=config['MONGO_URI'])

logging.basicConfig(
    filename=os.path.join(base_path, 'main_pipeline.log'),
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("main")

# ============= PIPELINES =============
def run_sagari(selected_steps=None, value_chain: ValueChain = None):
    """Pipeline SAGARI con 4 pasos."""
    workspace     = config['GEO_WORKSPACE']
    url_geoserver = config['URL_GEO']
    store         = config['GEO_STORE']
    user          = config['GEO_USER']
    password      = config['GEO_PWD']

    raw_input_path   = config['DATA']
    sagari_path      = os.path.join(base_path, FarmSource.SAGARI.value)
    output_path_get  = os.path.join(sagari_path, "01_get_data")
    output_path_qc   = os.path.join(sagari_path, "02_quality_control")
    output_path_buf  = os.path.join(sagari_path, "03_polygons_buffers")
    output_path_save = os.path.join(sagari_path, "04_save_farm")

    # Paso 1
    if selected_steps is None or 1 in selected_steps:
        log_print(logger, "Paso 1 (SAGARI): Obtener datos…")
        get_data_sagari(path_input=raw_input_path, output_file=output_path_get)

    # Paso 2
    if selected_steps is None or 2 in selected_steps:
        log_print(logger, "Paso 2 (SAGARI): Control de calidad…")
        quality_control_coordinates(
            output_path_get,
            output_path_qc,
            workspace,
            url_geoserver,
            store,
            user,
            password
        )

    # Paso 3
    if selected_steps is None or 3 in selected_steps:
        log_print(logger, "Paso 3 (SAGARI): Calcular buffers…")
        make_buffers(
            path_input=output_path_qc,
            path_output=output_path_buf,
            source=FarmSource.SAGARI
        )

    # Paso 4
    if selected_steps is None or 4 in selected_steps:
        log_print(logger, "Paso 4 (SAGARI): Guardar en MongoDB…")
        save_farm_sagari(output_path_buf, output_path_save, value_chain)


def run_geofarmer(selected_steps=None, value_chain: ValueChain = None):
    """
    Pipeline GEOFARMER con 3 pasos:
      1) Descargar datos desde API (solo boundaries verificados)
      2) Control de calidad (código externo, CRS, geometría, ADM3, centroide, área)
      3) Guardar en MongoDB (lee properties ya enriquecidas)
    """
    geofarmer_path   = os.path.join(base_path, FarmSource.GEOFARMER.value)
    output_api       = os.path.join(geofarmer_path, "01_get_data")
    output_qc        = os.path.join(geofarmer_path, "02_quality_control")
    output_errors    = os.path.join(geofarmer_path, "03_save_farm")

    # Carpeta compartida de shapefiles (descarga una sola vez)
    shapefiles_dir   = os.path.join(base_path, "shapefiles")

    # Asegurar shapefile ADM3 disponible (pasos 2 y eventuales)
    adm3_shp = ensure_adm3_shapefile(
        shapefiles_dir=shapefiles_dir,
        workspace=config['GEO_WORKSPACE'],
        url_geoserver=config['URL_GEO'],
        store=config['GEO_STORE'],
        user=config['GEO_USER'],
        password=config['GEO_PWD'],
    )
    if adm3_shp is None:
        # Fallback: intentar con la ruta estática del .env
        adm3_shp = config.get('ADM3_SHP_PATH')
        if not adm3_shp or not os.path.isfile(adm3_shp):
            raise FileNotFoundError(
                "No se pudo obtener el shapefile ADM3. "
                "Descárguelo manualmente o configure ADM3_SHP_PATH en .env"
            )
        log_print(logger, f"⚠️ Usando shapefile ADM3 del .env: {adm3_shp}")

    # Paso 1
    if selected_steps is None or 1 in selected_steps:
        log_print(logger, "Paso 1 (GEOFARMER): Descargando datos desde la API…")
        fetch_geofarmer_api.main(output_dir=output_api)

    # Paso 2
    if selected_steps is None or 2 in selected_steps:
        log_print(logger, "Paso 2 (GEOFARMER): Control de calidad + ADM3 + centroide…")
        qc_geofarmer(input_dir=output_api, output_dir=output_qc, adm3_shp=adm3_shp)

    # Paso 3
    if selected_steps is None or 3 in selected_steps:
        log_print(logger, "Paso 3 (GEOFARMER): Guardando farms y polígonos en MongoDB…")
        save_geofarmer(output_qc, output_errors, value_chain)


def main(source: FarmSource, selected_steps=None, value_chain: ValueChain = None):
    try:
        log_print(logger, f"Iniciando pipeline para fuente: {source.value}…")

        if source == FarmSource.SAGARI:
            valid = [1, 2, 3, 4]
            if selected_steps:
                invalid = [p for p in selected_steps if p not in valid]
                if invalid:
                    raise ValueError(f"Pasos no válidos para SAGARI: {invalid}. Usa {valid}.")
            run_sagari(selected_steps, value_chain)

        elif source == FarmSource.GEOFARMER:
            valid = [1, 2, 3]
            if selected_steps:
                invalid = [p for p in selected_steps if p not in valid]
                if invalid:
                    raise ValueError(f"Pasos no válidos para GEOFARMER: {invalid}. Usa {valid}.")
            run_geofarmer(selected_steps, value_chain)

        else:
            raise ValueError(f"Fuente no soportada: {source.value}")

        log_print(logger, "Proceso finalizado correctamente.")

    except Exception as e:
        log_print(logger, f"Error general en el proceso: {e}", level="error")
        raise


# ============= CLI =============
if __name__ == "__main__":
    valid_sources = [fs.value for fs in FarmSource]

    parser = argparse.ArgumentParser(
        description=(
            "Pipeline de procesamiento de datos de predios.\n\n"
            "SAGARI (pasos):\n"
            "  1: Obtener datos\n"
            "  2: Validación de coordenadas\n"
            "  3: Cálculo de buffers\n"
            "  4: Guardar en MongoDB\n\n"
            "GEOFARMER (pasos):\n"
            "  1: Descargar desde API (solo boundaries verificados)\n"
            "  2: Control de calidad (código externo, CRS, geometría, ADM3, centroide, área)\n"
            "  3: Guardar en MongoDB (lee properties ya enriquecidas)\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-s", "--source", type=str, required=True, choices=valid_sources,
        help=f"Fuente de los datos: {', '.join(valid_sources)}"
    )

    parser.add_argument(
        "-v", "--value_chain", type=str, required=True, choices=[valuevc.value for valuevc in ValueChain],
        help=f"Cadena de valor: {', '.join([valuevc.value for valuevc in ValueChain])}"
    )

    parser.add_argument(
        "-p", "--process", nargs='*', type=int,
        help='Número(s) de paso(s) a ejecutar. SAGARI: 1-4 | GEOFARMER: 1-3'
    )
    parser.add_argument(
        "-f", "--from_step", type=int,
        help='Paso desde el cual ejecutar. SAGARI: 1-4 | GEOFARMER: 1-3'
    )

    args = parser.parse_args()

    if args.process and args.from_step:
        raise ValueError("No se puede usar --process y --from_step al mismo tiempo. Usa solo uno.")

    source_enum = FarmSource(args.source)
    value_chain_enum = ValueChain(args.value_chain)

    if args.from_step is not None:
        max_step = 4 if source_enum == FarmSource.SAGARI else 3
        min_step = 1
        if not (min_step <= args.from_step <= max_step):
            raise ValueError(f"from_step fuera de rango para {source_enum.value}. Rango: {min_step}-{max_step}.")
        pasos = list(range(args.from_step, max_step + 1))
        print(f"🔁 Ejecutando desde el paso {args.from_step}: {pasos}")
        main(source_enum, selected_steps=pasos, value_chain=value_chain_enum)

    elif args.process:
        pasos = sorted(set(args.process))
        if source_enum == FarmSource.SAGARI:
            valid = [1, 2, 3, 4]
        else:
            valid = [1, 2, 3]
        pasos_validos = [p for p in pasos if p in valid]
        if not pasos_validos:
            raise ValueError(f"No se especificaron pasos válidos para {source_enum.value}.")
        print(f"🔁 Ejecutando pasos seleccionados: {pasos_validos}")
        main(source_enum, selected_steps=pasos_validos, value_chain=value_chain_enum)

    else:
        print("🔁 No se especificaron pasos. Se ejecutarán todos los pasos para la fuente seleccionada.")
        main(source_enum, value_chain=value_chain_enum)
