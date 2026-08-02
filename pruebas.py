# Script de pruebas que simula múltiples transmisiones con distintos tamaños
# y tasas de error para comparar el comportamiento de Hamming y CRC-32

import csv
import os
import random
import subprocess
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(RAIZ, "emisor"))

import crc32_utils
import emisor
import hamming

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Configuración de las simulaciones
LONGITUDES = [4, 8, 16, 32, 64]
TASAS = [0, 1 / 1000, 1 / 500, 1 / 200, 1 / 100, 1 / 50, 1 / 20, 1 / 10]
REPETICIONES = 100
M_HAMMING = 4
ALFABETO = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 "

SALIDA = os.path.join(RAIZ, "resultados")


# Busca el ejecutable del receptor compilado en la carpeta correspondiente
def ruta_receptor():
    for nombre in ("receptor", "receptor.exe"):
        ruta = os.path.join(RAIZ, "receptor", nombre)
        if os.path.exists(ruta):
            return ruta
    sys.exit("No se encontro el ejecutable del receptor. Corra 'make' en la carpeta receptor.")


# Genera un mensaje aleatorio con caracteres del alfabeto definido
def mensaje_aleatorio(largo):
    return "".join(random.choice(ALFABETO) for _ in range(largo))


# Construye una trama lista para enviarse, aplicando ruido según la probabilidad indicada
def construir_trama(texto, algoritmo, probabilidad):
    bits = emisor.codificar_mensaje(texto)
    if algoritmo == "hamming":
        trama, r, longitud = hamming.calcular_integridad(bits, M_HAMMING)
        header = "HAMMING:%d:%d:%d" % (M_HAMMING, r, longitud)
    else:
        trama = crc32_utils.calcular_integridad(bits)
        header = "CRC32:%d" % len(bits)

    con_ruido, flips = emisor.aplicar_ruido(trama, probabilidad)
    return header, "".join(str(b) for b in con_ruido), len(trama), flips


# Traduce una línea de salida del receptor a un resultado resumido
def clasificar(linea):
    """Traduce una linea de salida del receptor a (resultado, texto)."""
    if "sin errores" in linea or "Mensaje recuperado" in linea:
        return "entregado", linea[linea.index('"') + 1:linea.rindex('"')]
    return "descartado", None


# Ejecuta todas las tramas simuladas contra el receptor real de C++
def correr_casos(casos, receptor):
    entrada = "".join("%s\n%s\n" % (c["header"], c["bits"]) for c in casos)
    proceso = subprocess.run([receptor, "--stdin"], input=entrada,
                             stdout=subprocess.PIPE, universal_newlines=True)

    lineas = [l for l in proceso.stdout.splitlines() if l.startswith("[APLICACION]")]
    if len(lineas) != len(casos):
        sys.exit("El receptor devolvio %d resultados y se esperaban %d"
                 % (len(lineas), len(casos)))

    for caso, linea in zip(casos, lineas):
        resultado, texto = clasificar(linea)
        caso["resultado"] = resultado
        caso["correcto"] = (texto == caso["texto"])


# Genera la colección completa de casos a simular
def simular(receptor):
    casos = []
    for algoritmo in ("hamming", "crc32"):
        for largo in LONGITUDES:
            for tasa in TASAS:
                for _ in range(REPETICIONES):
                    texto = mensaje_aleatorio(largo)
                    header, bits, tam, flips = construir_trama(texto, algoritmo, tasa)
                    casos.append({"algoritmo": algoritmo, "largo": largo, "tasa": tasa,
                                  "texto": texto, "header": header, "bits": bits,
                                  "bits_trama": tam, "flips": flips})

    print("Procesando %d tramas con el receptor de C++..." % len(casos))
    correr_casos(casos, receptor)
    return casos


# Resume los resultados por combinación de algoritmo, longitud y tasa de error
def resumir(casos):
    """Agrupa por (algoritmo, largo, tasa) y calcula los porcentajes."""
    resumen = {}
    for caso in casos:
        clave = (caso["algoritmo"], caso["largo"], caso["tasa"])
        fila = resumen.setdefault(clave, {"total": 0, "correctas": 0, "descartadas": 0,
                                          "silenciosas": 0, "con_ruido": 0,
                                          "bits_datos": caso["largo"] * 8,
                                          "bits_trama": caso["bits_trama"]})
        fila["total"] += 1
        if caso["flips"] > 0:
            fila["con_ruido"] += 1
        if caso["resultado"] == "descartado":
            fila["descartadas"] += 1
        elif caso["correcto"]:
            fila["correctas"] += 1
        else:
            fila["silenciosas"] += 1
    return resumen


# Guarda los resultados agregados en un archivo CSV.
def guardar_csv(resumen, ruta):
    with open(ruta, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["algoritmo", "caracteres", "bits_datos", "bits_trama", "overhead_pct",
                    "tasa_error", "tramas", "tramas_con_ruido", "entregadas_correctas",
                    "descartadas", "entregas_erroneas"])
        for (algoritmo, largo, tasa) in sorted(resumen):
            fila = resumen[(algoritmo, largo, tasa)]
            overhead = 100.0 * (fila["bits_trama"] - fila["bits_datos"]) / fila["bits_datos"]
            w.writerow([algoritmo, largo, fila["bits_datos"], fila["bits_trama"],
                        round(overhead, 2), round(tasa, 5), fila["total"],
                        fila["con_ruido"], fila["correctas"], fila["descartadas"],
                        fila["silenciosas"]])


# Calcula el porcentaje de un campo de interés para una combinación específica
def porcentaje(resumen, algoritmo, tasa, campo):
    total = sum(f["total"] for (a, _, t), f in resumen.items() if a == algoritmo and t == tasa)
    valor = sum(f[campo] for (a, _, t), f in resumen.items() if a == algoritmo and t == tasa)
    return 100.0 * valor / total if total else 0.0


# Genera las etiquetas usadas en las gráficas para cada tasa de error
def etiquetas_tasas():
    return ["0" if t == 0 else "1/%d" % round(1 / t) for t in TASAS]


# Grafica el overhead de redundancia de ambos algoritmos
def grafica_overhead(ruta):
    x = [largo * 8 for largo in LONGITUDES]
    r = hamming.calcular_r(M_HAMMING)
    ham = [100.0 * r / M_HAMMING for _ in LONGITUDES]
    crc = [100.0 * 32 / bits for bits in x]

    plt.figure(figsize=(7, 4.5))
    plt.plot(x, ham, "o-", label="Hamming(%d,%d)" % (M_HAMMING + r, M_HAMMING))
    plt.plot(x, crc, "s-", label="CRC-32")
    plt.xlabel("Bits de datos del mensaje")
    plt.ylabel("Overhead (% de bits extra)")
    plt.title("Overhead de redundancia segun el tamano del mensaje")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(ruta, dpi=150)
    plt.close()


# Grafica la tasa de mensajes entregados correctamente según la tasa de error.
def grafica_entregas(resumen, ruta):
    x = range(len(TASAS))
    plt.figure(figsize=(7, 4.5))
    for algoritmo, marca in (("hamming", "o-"), ("crc32", "s-")):
        y = [porcentaje(resumen, algoritmo, t, "correctas") for t in TASAS]
        plt.plot(list(x), y, marca, label=algoritmo)
    plt.xticks(list(x), etiquetas_tasas())
    plt.xlabel("Tasa de error por bit")
    plt.ylabel("Mensajes entregados correctamente (%)")
    plt.title("Mensajes recuperados sin perdida de informacion")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(ruta, dpi=150)
    plt.close()


# Grafica la proporción de errores que no fueron detectados
def grafica_fallos(resumen, ruta):
    x = range(len(TASAS))
    plt.figure(figsize=(7, 4.5))
    for algoritmo, marca in (("hamming", "o-"), ("crc32", "s-")):
        y = [porcentaje(resumen, algoritmo, t, "silenciosas") for t in TASAS]
        plt.plot(list(x), y, marca, label=algoritmo)
    plt.xticks(list(x), etiquetas_tasas())
    plt.xlabel("Tasa de error por bit")
    plt.ylabel("Tramas aceptadas con datos erroneos (%)")
    plt.title("Errores que el algoritmo no logro detectar")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(ruta, dpi=150)
    plt.close()


# Grafica el efecto del tamaño del mensaje con una tasa fija de error
def grafica_por_longitud(resumen, ruta, tasa=1 / 100):
    plt.figure(figsize=(7, 4.5))
    for algoritmo, marca in (("hamming", "o-"), ("crc32", "s-")):
        y = []
        for largo in LONGITUDES:
            fila = resumen[(algoritmo, largo, tasa)]
            y.append(100.0 * fila["correctas"] / fila["total"])
        plt.plot([l * 8 for l in LONGITUDES], y, marca, label=algoritmo)
    plt.xlabel("Bits de datos del mensaje")
    plt.ylabel("Mensajes entregados correctamente (%)")
    plt.title("Efecto del tamano del mensaje con tasa de error 1/100")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(ruta, dpi=150)
    plt.close()


# Flujo principal del script: prepara, ejecuta y reporta los resultados
def main():
    random.seed(20260731)
    receptor = ruta_receptor()
    if not os.path.isdir(SALIDA):
        os.makedirs(SALIDA)

    casos = simular(receptor)
    resumen = resumir(casos)

    guardar_csv(resumen, os.path.join(SALIDA, "resultados.csv"))
    grafica_overhead(os.path.join(SALIDA, "overhead.png"))
    grafica_entregas(resumen, os.path.join(SALIDA, "entregas_correctas.png"))
    grafica_fallos(resumen, os.path.join(SALIDA, "errores_no_detectados.png"))
    grafica_por_longitud(resumen, os.path.join(SALIDA, "efecto_longitud.png"))

    print("\nResumen general por tasa de error:")
    print("%-10s %-8s %10s %10s %10s" % ("tasa", "algoritmo", "correctas", "descartadas", "erroneas"))
    for tasa, etiqueta in zip(TASAS, etiquetas_tasas()):
        for algoritmo in ("hamming", "crc32"):
            print("%-10s %-8s %9.1f%% %9.1f%% %9.1f%%"
                  % (etiqueta, algoritmo,
                     porcentaje(resumen, algoritmo, tasa, "correctas"),
                     porcentaje(resumen, algoritmo, tasa, "descartadas"),
                     porcentaje(resumen, algoritmo, tasa, "silenciosas")))

    print("\nArchivos generados en %s" % SALIDA)


if __name__ == "__main__":
    main()
