import os
import pandas as pd
from tqdm import tqdm
from ganabosques_orm.enums.species import Species
from ganabosques_orm.enums.ugg import UGG
from ganabosques_orm.enums.farmsource import FarmSource
from config import config
from ganabosques_orm.enums.source import Source

def get_data_sagari(path_input, output_file):
    print("Listando archivos en la carpeta de entrada...")
    archivos = [os.path.join(path_input, f) for f in os.listdir(path_input) if f.endswith(".xlsx")]

    if not archivos:
        print("No se encontraron archivos Excel en la ruta especificada.")
        return

    print(f"Se encontraron {len(archivos)} archivos:")
    for archivo in archivos:
        print(f" - {archivo}")

    lista_data = []
    print("Procesando archivos...")
    for archivo in tqdm(archivos):
        df = pd.read_excel(archivo, engine="openpyxl")

        # Mapeo de columnas a sumar por UGG x especie
        asignaciones = generar_agg_sagari(df)

        # ──> columnas base: SIT, coords y (si existe) CODIGO_RUV
        columnas_base = ["CODIGO_SIT", "LATITUD", "LONGITUD"]
        if "CODIGO_RUV" in df.columns:
            columnas_base.append("CODIGO_RUV")  # ← incluir RUV si está

        # columnas numéricas a sumar
        columnas_sumables = sum(asignaciones.values(), [])

        columnas_interes = columnas_base + columnas_sumables
        columnas_interes = [c for c in columnas_interes if c in df.columns]  # por si alguna falta

        print(f"\nLeyendo {archivo} con {df.shape[0]} filas y {df.shape[1]} columnas")

        df_filtrado = df[columnas_interes].copy()

        # crear columnas agregadas por cada clave (UGG_x_ESPECIE)
        for key, columnas_a_sumar in asignaciones.items():
            cols_presentes = [c for c in columnas_a_sumar if c in df_filtrado.columns]
            if cols_presentes:  # solo si hay algo que sumar
                df_filtrado[key] = df_filtrado[cols_presentes].sum(axis=1)
            else:
                # si no hay columnas para esa clave, crea la columna en 0 para mantener esquema
                df_filtrado[key] = 0

        # eliminar solo las columnas que fueron usadas para sumar (no toca CODIGO_RUV ni base)
        columnas_a_eliminar = [c for c in columnas_sumables if c in df_filtrado.columns]
        df_filtrado.drop(columns=columnas_a_eliminar, inplace=True)

        lista_data.append(df_filtrado)

    print("\nConcatenando todos los archivos...")
    df_final = pd.concat(lista_data, ignore_index=True)

    # Renombrar CODIGO_SIT → SIT_CODE (estándar del proyecto)
    df_final.rename(columns={"CODIGO_SIT": Source.SIT_CODE.value}, inplace=True)

    # Guardar
    print(f"Guardando archivo combinado en: {output_file}")
    os.makedirs(output_file, exist_ok=True)
    source = FarmSource.SAGARI.value
    df_final.to_csv(os.path.join(output_file, f"{source}_completo.csv"), index=False)
    print("✅ Proceso completado.")


def generar_agg_sagari(df):
    agg_dict = {}
    for grupo in UGG:
        for especie in Species:
            key = f"{grupo.name}_{especie.name}"
            substrings = [f"AFTOSA_{especie.name}_{suf}" for suf in config[FarmSource.SAGARI.value][grupo]]
            columnas = [col for col in df if any(substr in col for substr in substrings)]
            agg_dict[key] = columnas
    return agg_dict
