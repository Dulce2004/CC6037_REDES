# Emisor: APLICACION -> PRESENTACION -> ENLACE -> RUIDO -> TRANSMISION

import argparse
import random
import socket
import sys

import crc32_utils
import hamming


# APLICACION
# Solicita y valida la entrada del usuario para preparar el mensaje a enviar
def solicitar_mensaje(args):
    mensaje = args.mensaje
    binario = args.binario

    if mensaje is None and binario is None:
        opcion = input("Ingresar mensaje como (1) texto o (2) binario [1]: ").strip()
        if opcion == "2":
            binario = input("Ingrese el mensaje en binario: ").strip()
        else:
            mensaje = input("Ingrese el mensaje a enviar: ")

    algoritmo = args.algoritmo
    if algoritmo is None:
        algoritmo = input("Algoritmo a utilizar (hamming/crc32): ")
    algoritmo = algoritmo.strip().lower()
    if algoritmo not in ("hamming", "crc32"):
        sys.exit("Algoritmo no reconocido: " + algoritmo)

    m = args.m
    if algoritmo == "hamming" and m is None:
        entrada = input("Bits de datos por bloque m (Enter para default=4): ").strip()
        m = int(entrada) if entrada else 4
    if m is None:
        m = 4
    if m < 1:
        sys.exit("m debe ser mayor o igual a 1")

    tasa = args.tasa_error
    if tasa is None and args.forzar_flips is None:
        tasa = input("Tasa de error (formato 1/N, Enter para sin ruido): ")

    return mensaje, binario, algoritmo, m, (tasa.strip() if tasa else "")


# Convierte una tasa de error dada como "1/N" o como número decimal
def parsear_tasa_error(tasa):
    if not tasa:
        return 0.0
    if "/" in tasa:
        num, den = tasa.split("/")
        return float(num) / float(den)
    return float(tasa)


# PRESENTACION
# Convierte texto ASCII en una secuencia de bits de 8 bits por carácter
def codificar_mensaje(texto):
    """Codifica cada caracter en su ASCII binario de 8 bits."""
    bits = []
    for caracter in texto:
        if ord(caracter) > 127:
            sys.exit("El mensaje solo puede contener caracteres ASCII: '%s' no lo es" % caracter)
        for b in format(ord(caracter), "08b"):
            bits.append(int(b))
    return bits


# Lee una cadena binaria y la convierte en una lista de bits
def leer_binario(cadena):
    cadena = "".join(cadena.split())
    if not cadena or any(c not in "01" for c in cadena):
        sys.exit("El mensaje binario solo puede contener 0 y 1")
    if len(cadena) % 8 != 0:
        sys.exit("El mensaje binario debe ser multiplo de 8 bits (ASCII)")
    return [int(c) for c in cadena]


# RUIDO
# Introduce errores aleatorios en la trama según una probabilidad dada
def aplicar_ruido(bits, probabilidad):
    salida = list(bits)
    flips = 0
    if probabilidad > 0:
        for i in range(len(salida)):
            if random.random() < probabilidad:
                salida[i] ^= 1
                flips += 1
    return salida, flips


# Invierte bits específicos de la trama de forma manual
def forzar_errores(bits, posiciones):
    salida = list(bits)
    for p in posiciones:
        if p < 0 or p >= len(salida):
            sys.exit("Posicion fuera de la trama: %d (la trama tiene %d bits)" % (p, len(salida)))
        salida[p] ^= 1
    return salida


# TRANSMISION
# Envía la trama al receptor mediante un socket TCP
def enviar_informacion(host, puerto, header, bits):
    cadena = "".join(str(b) for b in bits)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, puerto))
        s.sendall(("%s\n%s\n" % (header, cadena)).encode("utf-8"))
    return cadena


# Construye los argumentos de línea de comandos del programa
def construir_argumentos():
    parser = argparse.ArgumentParser(description="Emisor - Laboratorio 2 Redes")
    parser.add_argument("--mensaje", help="texto a enviar")
    parser.add_argument("--binario", help="mensaje ya en binario, en lugar de texto")
    parser.add_argument("--algoritmo", choices=["hamming", "crc32"])
    parser.add_argument("--m", type=int, help="bits de datos por bloque de Hamming")
    parser.add_argument("--tasa-error", dest="tasa_error",
                        help="probabilidad de error por bit, formato 1/N")
    parser.add_argument("--forzar-flips", dest="forzar_flips",
                        help="posiciones de bits a invertir, separadas por coma")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--manual", action="store_true",
                        help="no usa sockets: imprime la trama para pasarla a mano al receptor")
    return parser.parse_args()


# Flujo principal del emisor: prepara, codifica, protege y transmite la trama
def main():
    args = construir_argumentos()
    mensaje, binario, algoritmo, m, tasa = solicitar_mensaje(args)

    if binario is not None:
        bits_mensaje = leer_binario(binario)
        origen = "binario"
    else:
        bits_mensaje = codificar_mensaje(mensaje)
        origen = "'%s'" % mensaje

    print("\n[APLICACION]   Mensaje: %s | Algoritmo: %s" % (origen, algoritmo))
    print("[PRESENTACION] Binario ASCII (%d bits): %s"
          % (len(bits_mensaje), "".join(str(b) for b in bits_mensaje)))

    if algoritmo == "hamming":
        trama, r, longitud = hamming.calcular_integridad(bits_mensaje, m)
        header = "HAMMING:%d:%d:%d" % (m, r, longitud)
        print("[ENLACE]       Hamming(%d,%d) -> %d bloques, %d bits de trama"
              % (m + r, m, len(trama) // (m + r), len(trama)))
    else:
        trama = crc32_utils.calcular_integridad(bits_mensaje)
        header = "CRC32:%d" % len(bits_mensaje)
        print("[ENLACE]       CRC-32 -> %d bits (%d de datos + 32 de checksum)"
              % (len(trama), len(bits_mensaje)))

    print("[ENLACE]       Trama con integridad: " + "".join(str(b) for b in trama))

    if args.forzar_flips:
        posiciones = [int(p) for p in args.forzar_flips.split(",") if p.strip()]
        trama_final = forzar_errores(trama, posiciones)
        print("[RUIDO]        Bits invertidos manualmente en: %s" % posiciones)
    else:
        probabilidad = parsear_tasa_error(tasa)
        trama_final, flips = aplicar_ruido(trama, probabilidad)
        print("[RUIDO]        Probabilidad %s -> %d bit(s) invertido(s)" % (probabilidad, flips))

    print("[RUIDO]        Trama transmitida: " + "".join(str(b) for b in trama_final))

    if args.manual:
        print("\n[TRANSMISION]  Modo manual, la trama no se envio por socket.")
        print("Copie estas dos lineas en el receptor (./receptor --stdin):\n")
        print(header)
        print("".join(str(b) for b in trama_final))
        print()
    else:
        enviar_informacion(args.host, args.port, header, trama_final)
        print("[TRANSMISION]  Enviado a %s:%d con header '%s'\n"
              % (args.host, args.port, header))


if __name__ == "__main__":
    main()
