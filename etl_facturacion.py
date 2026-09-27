import csv
import os
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


ARCHIVO_CSV = Path(__file__).resolve().parent / "citas_dia.csv"
COLUMNAS_CSV = {"paciente", "especialidad", "costo_base"}
CENTAVO = Decimal("0.01")


def obtener_configuracion():
    """Carga .env y obtiene los parámetros de conexión."""
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError(
            "Falta python-dotenv. Instálalo con: pip install python-dotenv"
        ) from exc

    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)
    usuario = os.getenv("MYSQL_USER")
    contrasena = os.getenv("MYSQL_PASSWORD")
    if not usuario or not contrasena:
        raise ValueError("Debes definir MYSQL_USER y MYSQL_PASSWORD en .env.")

    try:
        puerto = int(os.getenv("MYSQL_PORT", "3306"))
    except ValueError as exc:
        raise ValueError("MYSQL_PORT debe ser un número entero.") from exc

    if not 1 <= puerto <= 65535:
        raise ValueError("MYSQL_PORT debe estar entre 1 y 65535.")

    return {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": puerto,
        "user": usuario,
        "password": contrasena,
        "database": "saludMedica",
    }


def conectar_mysql(configuracion):
    """Abre la conexión a la base de datos existente."""
    try:
        import mysql.connector
    except ImportError as exc:
        raise RuntimeError(
            "Falta mysql-connector-python. Instálalo con: pip install mysql-connector-python"
        ) from exc

    return mysql.connector.connect(**configuracion)


def crear_tabla(conexion):
    """Crea la tabla de destino si todavía no existe."""
    sentencia = """
        CREATE TABLE IF NOT EXISTS facturacion_citas (
            id INT AUTO_INCREMENT PRIMARY KEY,
            paciente VARCHAR(255) NOT NULL,
            especialidad VARCHAR(100) NOT NULL,
            costo_base DECIMAL(10, 2) NOT NULL,
            descuento DECIMAL(10, 2) NOT NULL,
            igv DECIMAL(10, 2) NOT NULL,
            total DECIMAL(10, 2) NOT NULL
        )
    """
    cursor = conexion.cursor()
    try:
        cursor.execute(sentencia)
        conexion.commit()
    finally:
        cursor.close()


def transformar_fila(fila):
    """Valida una cita y devuelve los valores listos para insertar."""
    paciente = (fila["paciente"] or "").strip()
    especialidad = (fila["especialidad"] or "").strip()
    if not especialidad:
        raise ValueError("especialidad vacía")

    try:
        costo_base = Decimal((fila["costo_base"] or "").strip())
        if not costo_base.is_finite():
            raise ValueError("costo_base debe ser un número finito")
        costo_base = costo_base.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("costo_base no es un número válido") from exc

    if costo_base <= 0:
        raise ValueError("costo_base debe ser mayor que cero")

    descuento = (
        costo_base * Decimal("0.10") if especialidad == "Pediatria" else Decimal("0")
    ).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    costo_descontado = costo_base - descuento
    igv = (costo_descontado * Decimal("0.18")).quantize(
        CENTAVO, rounding=ROUND_HALF_UP
    )
    total = costo_descontado + igv

    return (paciente, especialidad, costo_base, descuento, igv, total)


def cargar_citas(conexion, ruta_csv):
    """Lee el CSV, descarta filas inválidas e inserta las citas válidas."""
    insertar = """
        INSERT INTO facturacion_citas
            (paciente, especialidad, costo_base, descuento, igv, total)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    exitosos = 0
    descartados = 0

    with ruta_csv.open("r", encoding="utf-8-sig", newline="") as archivo:
        lector = csv.DictReader(archivo, delimiter=",")
        if lector.fieldnames is None or not COLUMNAS_CSV.issubset(lector.fieldnames):
            raise ValueError(
                "El CSV debe tener los encabezados paciente,especialidad,costo_base."
            )

        cursor = conexion.cursor()
        try:
            for fila in lector:
                try:
                    valores = transformar_fila(fila)
                except (ValueError, KeyError, AttributeError) as exc:
                    descartados += 1
                    print(f"Línea {lector.line_num} descartada: {exc}")
                    continue

                # Si falla MySQL, se detiene la carga: no se cuenta la fila como éxito.
                cursor.execute(insertar, valores)
                conexion.commit()
                exitosos += 1
        finally:
            cursor.close()

    return exitosos, descartados


def main():
    """Coordina la preparación, transformación, carga y reporte."""
    conexion = None
    try:
        if not ARCHIVO_CSV.is_file():
            raise FileNotFoundError(f"No se encontró el archivo CSV: {ARCHIVO_CSV}")

        configuracion = obtener_configuracion()
        conexion = conectar_mysql(configuracion)
        crear_tabla(conexion)
        exitosos, descartados = cargar_citas(conexion, ARCHIVO_CSV)
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Los errores de conexión o inserción impiden informar una carga completa.
        print(f"Error de base de datos: {exc}", file=sys.stderr)
        return 1
    finally:
        if conexion is not None:
            conexion.close()

    print(f"Registros procesados con éxito: {exitosos}")
    print(f"Registros descartados por errores: {descartados}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
