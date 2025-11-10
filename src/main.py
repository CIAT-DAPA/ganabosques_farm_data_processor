# -*- coding: utf-8 -*-
import logging
import os
import argparse

from config import config
from tools.log_print import log_print
from ganabosques_orm.enums.farmsource import FarmSource

# ====== SAGARI ======
from get_data import get_data_sagari
from quality_control_coordinates.quality_control_coordinates import quality_control_coordinates
from polygons_buffers.polygons_buffers import buffer as make_buffers
from save_farm.save_farm import save_farm as save_farm_sagari

# ====== GEOFARMER ======
# Paso 1: descarga desde API (importa el módulo completo)
from get_data import get_data_geofarmer as fetch_geofarmer_api
# Paso 2: unión por SIT
from quality_control_coordinates.quality_control_geofarmer import procesar as union_sit_geofarmer
# Paso 3: guardar en MongoDB
from save_farm.save_farm_geofarmer import run as save_geofarmer

# ============= LOG/OUTPUT BASE =============
base_path = os.path.join(config['WORKSPACE'], "farm", "outputs")
os.makedirs(base_path, exist_ok=True)

logging.basicConfig(
    filename=os.path.join(base_path, 'main_pipeline.log'),
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("main")

# ============= PIPELINES =============
def run_sagari(selected_steps=None):
    """Pipeline SAGARI con 4 pasos."""
    workspace     = config['GEO_WORKSPACE']
    url_geoserver = config['URL_GEO']
    store         = config['GEO_STORE']
    user          = config['GEO_USER']
    password      = config['GEO_PWD']

    raw_input_path   = config['DATA']
    output_path_get  = os.path.join(base_path, "01_tmp_get_data_sagari")
    output_path_qc   = os.path.join(base_path, "02_tmp_quality_control_coordinates")
    output_path_buf  = os.path.join(base_path, "03_tmp_polygons_buffers_data_frame")
    output_path_save = os.path.join(base_path, "04_tmp_save_movement")

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
        save_farm_sagari(output_path_buf, output_path_save)


def run_geofarmer(selected_steps=None):
    """
    Pipeline GEOFARMER con 3 pasos:
      1) Descargar datos desde API (get_data_geofarmer)
      2) Unir polígonos por SIT (EPSG:3116)
      3) Guardar en MongoDB (centroide WGS84, área ha, ADM3)
    """
    polygons_dir  = config['GEOFARMER_POLYGONS_DIR']
    adm3_shp_path = config['ADM3_SHP_PATH']
    errors_dir    = config.get('GEOFARMER_ERRORS_DIR') or os.path.join(base_path, "geofarmer_errores")

    # Paso 1
    if selected_steps is None or 1 in selected_steps:
        log_print(logger, "Paso 1 (GEOFARMER): Descargando datos desde la API…")
        # 🔹 Aquí se llama a la función main del módulo
        fetch_geofarmer_api.main()

    # Paso 2
    if selected_steps is None or 2 in selected_steps:
        log_print(logger, "Paso 2 (GEOFARMER): Uniendo polígonos por SIT…")
        union_sit_geofarmer()

    # Paso 3
    if selected_steps is None or 3 in selected_steps:
        log_print(logger, "Paso 3 (GEOFARMER): Guardando farms y polígonos en MongoDB…")
        save_geofarmer(polygons_dir, adm3_shp_path, errors_dir)


def main(source: FarmSource, selected_steps=None):
    try:
        log_print(logger, f"Iniciando pipeline para fuente: {source.value}…")

        if source == FarmSource.SAGARI:
            valid = [1, 2, 3, 4]
            if selected_steps:
                invalid = [p for p in selected_steps if p not in valid]
                if invalid:
                    raise ValueError(f"Pasos no válidos para SAGARI: {invalid}. Usa {valid}.")
            run_sagari(selected_steps)

        elif source == FarmSource.GEOFARMER:
            valid = [1, 2, 3]
            if selected_steps:
                invalid = [p for p in selected_steps if p not in valid]
                if invalid:
                    raise ValueError(f"Pasos no válidos para GEOFARMER: {invalid}. Usa {valid}.")
            run_geofarmer(selected_steps)

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
            "  1: Descargar desde API (get_data_geofarmer)\n"
            "  2: Unión de polígonos por SIT (EPSG:3116)\n"
            "  3: Guardar en MongoDB (centroide WGS84, área ha, ADM3)\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-s", "--source", type=str, required=True, choices=valid_sources,
        help=f"Fuente de los datos: {', '.join(valid_sources)}"
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

    if args.from_step is not None:
        max_step = 4 if source_enum == FarmSource.SAGARI else 3
        min_step = 1
        if not (min_step <= args.from_step <= max_step):
            raise ValueError(f"from_step fuera de rango para {source_enum.value}. Rango: {min_step}-{max_step}.")
        pasos = list(range(args.from_step, max_step + 1))
        print(f"🔁 Ejecutando desde el paso {args.from_step}: {pasos}")
        main(source_enum, selected_steps=pasos)

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
        main(source_enum, selected_steps=pasos_validos)

    else:
        print("🔁 No se especificaron pasos. Se ejecutarán todos los pasos para la fuente seleccionada.")
        main(source_enum)
