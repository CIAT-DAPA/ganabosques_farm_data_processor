import os
import pandas as pd
from unidecode import unidecode
from tqdm import tqdm

def get_data_sagari(path_input, output_file):
    print("Listando archivos en la carpeta de entrada...")
    archivos = [os.path.join(path_input, f) for f in os.listdir(path_input) if f.endswith(".xlsx")]

    if not archivos:
        print("No se encontraron archivos Excel en la ruta especificada.")
        return

    print(f"Se encontraron {len(archivos)} archivos:")
    for archivo in archivos:
        print(f" - {archivo}")

    columnas_interes = [
        "CODIGO_RUV", "CODIGO_SIT", "LATITUD", "LONGITUD", "DEPARTAMENTO", "MUNICIPIO", "VEREDA",
        "TOTAL_AFTOSA_BOVINOS", "TOTAL_AFTOSA_BOVINOS_NV", "TOTAL_AFTOSA_BUFALINOS","TOTAL_AFTOSA_BUFALINOS_NV",
        
        # ─ BOVINOS vacunados
        "AFTOSA_BOVINOS_HEMBRAS_MENORES_A_3_MESES","AFTOSA_BOVINOS_HEMBRAS_MENORES_DE_3_A_8_MESES",
        "AFTOSA_BOVINOS_DE_8_A_12_MESES","AFTOSA_BOVINOS_HEMBRAS_1___2_AÑOS","AFTOSA_BOVINOS_HEMBRAS_2___3_AÑOS",
        "AFTOSA_BOVINOS_HEMBRAS_3___5_AÑOS","AFTOSA_BOVINOS_HEMBRAS_MAYORES_A_5_AÑOS",
        "AFTOSA_BOVINOS_TERNEROS_MENORES_A_1_AÑO","AFTOSA_BOVINOS_MACHOS_1___2_AÑOS","AFTOSA_BOVINOS_MACHOS_2___3_AÑOS",
        "AFTOSA_BOVINOS_MACHOS_MAYORES_A_3_AÑOS","AFTOSA_BOVINOS_MACHOS_MENORES_A_3_MESES",
        "AFTOSA_BOVINOS_MACHOS_3_HASTA_8_MESES","AFTOSA_BOVINOS_MACHOS_8_HASTA_12_MESES",
        # ─ BOVINOS no vacunados
        "AFTOSA_BOVINOS_HEMBRAS_MENORES_A_3_MESES_NV","AFTOSA_BOVINOS_HEMBRAS_MENORES_DE_3_A_8_MESES_NV",
        "AFTOSA_BOVINOS_DE_8_A_12_MESES_NV","AFTOSA_BOVINOS_HEMBRAS_1___2_AÑOS_NV","AFTOSA_BOVINOS_HEMBRAS_2___3_AÑOS_NV",
        "AFTOSA_BOVINOS_HEMBRAS_3___5_AÑOS_NV","AFTOSA_BOVINOS_HEMBRAS_MAYORES_A_5_AÑOS_NV",
        "AFTOSA_BOVINOS_TERNEROS_MENORES_A_1_AÑO_NV","AFTOSA_BOVINOS_MACHOS_1___2_AÑOS_NV","AFTOSA_BOVINOS_MACHOS_2___3_AÑOS_NV",
        "AFTOSA_BOVINOS_MACHOS_MAYORES_A_3_AÑOS_NV","AFTOSA_BOVINOS_MACHOS_MENORES_A_3_MESES_NV",
        "AFTOSA_BOVINOS_MACHOS_3_HASTA_8_MESES_NV","AFTOSA_BOVINOS_MACHOS_8_HASTA_12_MESES_NV",
        # ─ BUFALINOS vacunados
        "AFTOSA_BUFALINOS_HEMBRAS_MENORES_A_3_MESES","AFTOSA_BUFALINOS_HEMBRAS_MENORES_DE_3_A_8_MESES",
        "AFTOSA_BUFALINOS_DE_8_A_12_MESES","AFTOSA_BUFALINOS_HEMBRAS_1___2_AÑOS","AFTOSA_BUFALINOS_HEMBRAS_2___3_AÑOS",
        "AFTOSA_BUFALINOS_HEMBRAS_3___5_AÑOS","AFTOSA_BUFALINOS_HEMBRAS_MAYORES_A_5_AÑOS",
        "AFTOSA_BUFALINOS_MACHOS_1___2_AÑOS","AFTOSA_BUFALINOS_MACHOS_2___3_AÑOS","AFTOSA_BUFALINOS_MACHOS_MAYORES_A_3_AÑOS",
        "AFTOSA_BUFALINOS_MACHOS_MENORES_A_3_MESES","AFTOSA_BUFALINOS_MACHOS_3_HASTA_8_MESES","AFTOSA_BUFALINOS_MACHOS_8_HASTA_12_MESES",
        # ─ BUFALINOS no vacunados
        "AFTOSA_BUFALINOS_HEMBRAS_MENORES_A_3_MESES_NV","AFTOSA_BUFALINOS_HEMBRAS_MENORES_DE_3_A_8_MESES_NV",
        "AFTOSA_BUFALINOS_DE_8_A_12_MESES_NV","AFTOSA_BUFALINOS_HEMBRAS_1___2_AÑOS_NV","AFTOSA_BUFALINOS_HEMBRAS_2___3_AÑOS_NV",
        "AFTOSA_BUFALINOS_HEMBRAS_3___5_AÑOS_NV","AFTOSA_BUFALINOS_HEMBRAS_MAYORES_A_5_AÑOS_NV",
        "AFTOSA_BUFALINOS_MACHOS_1___2_AÑOS_NV","AFTOSA_BUFALINOS_MACHOS_2___3_AÑOS_NV","AFTOSA_BUFALINOS_MACHOS_MAYORES_A_3_AÑOS_NV",
        "AFTOSA_BUFALINOS_MACHOS_MENORES_A_3_MESES_NV","AFTOSA_BUFALINOS_MACHOS_3_HASTA_8_MESES_NV","AFTOSA_BUFALINOS_MACHOS_8_HASTA_12_MESES_NV"
    ]

    agg_dict = {
        # ─ BOVINOS vacunados y no vacunados
        "BOV_terneros_menores_1_anio": [
            "AFTOSA_BOVINOS_HEMBRAS_MENORES_A_3_MESES","AFTOSA_BOVINOS_HEMBRAS_MENORES_DE_3_A_8_MESES",
            "AFTOSA_BOVINOS_DE_8_A_12_MESES","AFTOSA_BOVINOS_TERNEROS_MENORES_A_1_AÑO",
            "AFTOSA_BOVINOS_MACHOS_8_HASTA_12_MESES","AFTOSA_BOVINOS_MACHOS_MENORES_A_3_MESES",
            "AFTOSA_BOVINOS_MACHOS_3_HASTA_8_MESES","AFTOSA_BOVINOS_HEMBRAS_MENORES_A_3_MESES_NV",
            "AFTOSA_BOVINOS_HEMBRAS_MENORES_DE_3_A_8_MESES_NV","AFTOSA_BOVINOS_DE_8_A_12_MESES_NV",
            "AFTOSA_BOVINOS_TERNEROS_MENORES_A_1_AÑO_NV","AFTOSA_BOVINOS_MACHOS_8_HASTA_12_MESES_NV",
            "AFTOSA_BOVINOS_MACHOS_MENORES_A_3_MESES_NV","AFTOSA_BOVINOS_MACHOS_3_HASTA_8_MESES_NV"
        ],
        "BOV_hembras_machos_1_2_anios": [
            "AFTOSA_BOVINOS_HEMBRAS_1___2_AÑOS","AFTOSA_BOVINOS_MACHOS_1___2_AÑOS",
            "AFTOSA_BOVINOS_HEMBRAS_1___2_AÑOS_NV","AFTOSA_BOVINOS_MACHOS_1___2_AÑOS_NV"
        ],
        "BOV_hembras_2_3_anios": ["AFTOSA_BOVINOS_HEMBRAS_2___3_AÑOS","AFTOSA_BOVINOS_HEMBRAS_2___3_AÑOS_NV"],
        "BOV_machos_2_3_anios": ["AFTOSA_BOVINOS_MACHOS_2___3_AÑOS","AFTOSA_BOVINOS_MACHOS_2___3_AÑOS_NV"],
        "BOV_hembras_mayores_3_anios": [
            "AFTOSA_BOVINOS_HEMBRAS_3___5_AÑOS","AFTOSA_BOVINOS_HEMBRAS_MAYORES_A_5_AÑOS",
            "AFTOSA_BOVINOS_HEMBRAS_3___5_AÑOS_NV","AFTOSA_BOVINOS_HEMBRAS_MAYORES_A_5_AÑOS_NV"
        ],
        "BOV_machos_mayores_3_anios": [
            "AFTOSA_BOVINOS_MACHOS_MAYORES_A_3_AÑOS","AFTOSA_BOVINOS_MACHOS_MAYORES_A_3_AÑOS_NV"
        ],
        # ─ BUFALINOS vacunados y no vacunados
        "BUF_terneros_menores_1_anio": [
            "AFTOSA_BUFALINOS_HEMBRAS_MENORES_A_3_MESES","AFTOSA_BUFALINOS_HEMBRAS_MENORES_DE_3_A_8_MESES",
            "AFTOSA_BUFALINOS_DE_8_A_12_MESES","AFTOSA_BUFALINOS_MACHOS_MENORES_A_3_MESES",
            "AFTOSA_BUFALINOS_MACHOS_3_HASTA_8_MESES","AFTOSA_BUFALINOS_MACHOS_8_HASTA_12_MESES",
            "AFTOSA_BUFALINOS_HEMBRAS_MENORES_A_3_MESES_NV","AFTOSA_BUFALINOS_HEMBRAS_MENORES_DE_3_A_8_MESES_NV",
            "AFTOSA_BUFALINOS_DE_8_A_12_MESES_NV","AFTOSA_BUFALINOS_MACHOS_MENORES_A_3_MESES_NV",
            "AFTOSA_BUFALINOS_MACHOS_3_HASTA_8_MESES_NV","AFTOSA_BUFALINOS_MACHOS_8_HASTA_12_MESES_NV"
        ],
        "BUF_hembras_machos_1_2_anios": ["AFTOSA_BUFALINOS_HEMBRAS_1___2_AÑOS","AFTOSA_BUFALINOS_HEMBRAS_1___2_AÑOS_NV",
                                         "AFTOSA_BUFALINOS_MACHOS_1___2_AÑOS","AFTOSA_BUFALINOS_MACHOS_1___2_AÑOS_NV"],
        "BUF_hembras_2_3_anios": ["AFTOSA_BUFALINOS_HEMBRAS_2___3_AÑOS","AFTOSA_BUFALINOS_HEMBRAS_2___3_AÑOS_NV"],
        "BUF_hembras_mayores_3_anios": ["AFTOSA_BUFALINOS_HEMBRAS_3___5_AÑOS","AFTOSA_BUFALINOS_HEMBRAS_MAYORES_A_5_AÑOS",
                                       "AFTOSA_BUFALINOS_HEMBRAS_3___5_AÑOS_NV","AFTOSA_BUFALINOS_HEMBRAS_MAYORES_A_5_AÑOS_NV"],
        
        "BUF_machos_2_3_anios": ["AFTOSA_BUFALINOS_MACHOS_2___3_AÑOS","AFTOSA_BUFALINOS_MACHOS_2___3_AÑOS_NV"],
        "BUF_machos_mayores_3_anios": ["AFTOSA_BUFALINOS_MACHOS_MAYORES_A_3_AÑOS","AFTOSA_BUFALINOS_MACHOS_MAYORES_A_3_AÑOS_NV"]
    }

    lista_data = []
    print("Procesando archivos...")
    for archivo in tqdm(archivos):
        df = pd.read_excel(archivo, engine="openpyxl")
        print(f"\nLeyendo {archivo} con {df.shape[0]} filas y {df.shape[1]} columnas")

        # Seleccionar columnas que interesan (solo las que existen en el df)
        columnas_en_df = [col for col in columnas_interes if col in df.columns]
        df = df[columnas_en_df]

        # Limpieza de columnas de texto: quitar espacios y tildes
        for col in ["DEPARTAMENTO", "MUNICIPIO", "VEREDA"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()            # Quitar espacios en extremos
                df[col] = df[col].apply(unidecode)                    # Normalizar caracteres (tildes, ñ, etc)
                df[col] = df[col].str.replace(r'[^a-zA-Z0-9\s]', '', regex=True)  # Opcional: eliminar caracteres no alfanuméricos

        # Aquí sigue el resto de tu procesamiento...
        for nueva_col, cols_originales in agg_dict.items():
            cols_validas = [col for col in cols_originales if col in df.columns]

            print(f"\n➤ Agregando columna: {nueva_col}")
            print(f"   Columnas encontradas para sumar: {cols_validas}")

            if cols_validas:
                # Asegurar que todas las columnas sean numéricas
                df[cols_validas] = df[cols_validas].apply(pd.to_numeric, errors='coerce').fillna(0)
                df[nueva_col] = df[cols_validas].sum(axis=1)
            else:
                print(f"   ⚠️ No se encontraron columnas para {nueva_col}, asignando 0")
                df[nueva_col] = 0

            # Eliminar columnas originales usadas en esta agregación
            df.drop(columns=cols_validas, inplace=True, errors='ignore')

        lista_data.append(df)

    # Concatenar todos los datos
    print("\nConcatenando todos los archivos...")
    df_final = pd.concat(lista_data, ignore_index=True)

    # Guardar archivo final
    print(f"Guardando archivo combinado en: {output_file}")
    #output_path = os.path.join(output_file, "01_tmp_get_data_sagari")
    os.makedirs(output_file, exist_ok=True)

    df_final.to_csv(os.path.join(output_file, "sagari_completo.csv"), index=False)
    print("✅ Proceso completado.")

# Ejemplo de uso
""" get_data_sagari(
    path_input=r"D:\OneDrive - CGIAR\Desktop\ganabosques\farms\input\sagari\brutos",
    output_file=r"D:\OneDrive - CGIAR\Desktop\ganabosques\farms\tmp"
) """