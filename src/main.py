import logging
import os
import argparse

from get_data_sagari import get_data_sagari
from quality_control_coordinates import quality_control_coordinates
from polygons_buffers import buffer
from save_farm import save_farm
from tools.log_print import log_print
from config import config

from ganabosques_orm.enums.farmsource import FarmSource

#crear carpeta base donde guardar la informacion
base_path = os.path.join(config['WORKSPACE'], "farm", "outputs")
os.makedirs(base_path, exist_ok=True)

logging.basicConfig(
    filename=os.path.join(base_path, 'main_pipeline.log'),
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("main")

def main(source, selected_steps=None):
    try:
        log_print(logger, "Iniciando pipeline Farm Data Processor...")

        workspace=config['GEO_WORKSPACE']
        url_geoserver= config['URL_GEO']
        store=config['GEO_STORE']
        user = config['GEO_USER']
        password= config['GEO_PWD']

        # Definir rutas donde esta la data
        raw_input_path = config['DATA']

        output_path_get_data = os.path.join(base_path, "01_tmp_get_data_sagari")
        output_path_quality = os.path.join(base_path, "02_tmp_quality_control_coordinates")
        output_path_buffers = os.path.join(base_path, "03_tmp_polygons_buffers_data_frame")
        output_path_save = os.path.join(base_path, "04_tmp_save_movement")

        if selected_steps is None or 1 in selected_steps:
        # Paso 1: Obtener datos
            log_print(logger, "Paso 1: Obtener datos...")
            get_data_sagari(
                path_input=raw_input_path,
                output_file=output_path_get_data
            )

        if selected_steps is None or 2 in selected_steps:
        # Paso 2: Validación de calidad
            log_print(logger, "Paso 2: Validación de calidad...")
            quality_control_coordinates( 
                output_path_get_data, 
                output_path_quality,
                workspace,
                url_geoserver,
                store,
                user,
                password
            )

        if selected_steps is None or 3 in selected_steps:
        # Paso 3: Calcular buffers
            log_print(logger, "Paso 3: Calcular buffers...")
            buffer(
                path_input=output_path_quality,
                path_output=output_path_buffers,
                source=source
            )

        if selected_steps is None or 4 in selected_steps:
        # Paso 4: guardar resultados
            log_print(logger, "Paso 4: Guardar farms...")
            save_farm(output_path_buffers, output_path_save)
        
        log_print(logger, "Proceso finalizado.")

    except Exception as e:
        log_print(logger, f"Error general en el proceso: {e}", level="error")

if __name__ == "__main__":

    valid_sources = [fs.value for fs in FarmSource]
    parser = argparse.ArgumentParser(
    description=(
        "Pipeline de procesamiento de datos de movilización ganadera.\n\n"
        "Pasos disponibles:\n"
        "  1: Obtener datos de la fuente\n"
        "  2: Validación de calidad de coordenadas\n"
        "  3: Cálculo de buffers geoespaciales\n"
        "  4: Guardar información de predios en MongoDB\n\n"
        "Ejemplos de uso:\n"
        "  python main.py -s SAGARI             # Ejecuta todos los pasos\n"
        "  python main.py -s SAGARI -p 1 3      # Ejecuta solo los pasos 1 y 3\n"
        "  python main.py -s SAGARI -f 2        # Ejecuta desde el paso 2 en adelante\n"
    ),
    formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        '-p', '--process', nargs='*', type=int,
        help='Número(s) de paso(s) a ejecutar (1-4). Si se omite, se ejecutan todos.'
    )
    parser.add_argument(
        '-f', '--from_step', type=int, choices=[1, 2, 3, 4],
        help='Paso desde el cual ejecutar el pipeline (1-4).'
    )
    parser.add_argument(
        "-s", "--source", type=str, required=True, choices=valid_sources,
        help=f"Fuente de los datos. Opciones disponibles: {', '.join(valid_sources)}",
    )

    args = parser.parse_args()

    if args.process and args.from_step:
        raise ValueError("No se puede usar --process y --from al mismo tiempo. Usa solo uno.")
    
    source_enum = FarmSource(args.source)

    if args.from_step:
        pasos = list(range(args.from_step, 5)) 
        print(f"🔁 Se procederá a ejecutar los pasos desde el paso {args.from_step} en adelante: {pasos}")
        main(source_enum, selected_steps=pasos)
    elif args.process:
        pasos = list(set(args.process))  # eliminar duplicados
        pasos_validos = [p for p in pasos if p in [1, 2, 3, 4]]
        if not pasos_validos:
            raise ValueError("No se especificaron pasos válidos. Usa 1, 2, 3 o 4.")
        print(f"🔁 Se procederá a ejecutar los pasos seleccionados: {pasos_validos}")
        main(source_enum, selected_steps=sorted(pasos_validos))
    else:
        print("🔁 No se especificaron pasos. Se ejecutarán todos los pasos (1-4).")
        main(source_enum)