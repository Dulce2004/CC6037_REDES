# Calcula la cantidad de bits de paridad necesarios para un bloque de Hamming
def calcular_r(m):
    r = 1
    while (m + r + 1) > (1 << r):
        r += 1
    return r


# Verifica si un número es una potencia de dos
def es_potencia_de_dos(x):
    return x != 0 and (x & (x - 1)) == 0


# Codifica un bloque de datos usando Hamming con bits de paridad
def codificar_bloque(datos, m, r):
    n = m + r
    bloque = [0] * (n + 1)

    idx = 0
    # Coloca los bits de datos en las posiciones que no son potencias de dos
    for pos in range(1, n + 1):
        if not es_potencia_de_dos(pos):
            bloque[pos] = datos[idx]
            idx += 1

    # Calcula cada bit de paridad según las posiciones que cubre
    for k in range(r):
        p = 1 << k
        paridad = 0
        for pos in range(1, n + 1):
            if pos != p and (pos & p):
                paridad ^= bloque[pos]
        bloque[p] = paridad

    return bloque[1:]


# Codifica todo el mensaje en bloques de Hamming y devuelve la trama completa
def calcular_integridad(bits_mensaje, m):
    """Codifica el mensaje en bloques de Hamming(n, m).

    Retorna (trama, r, longitud_original).
    """
    r = calcular_r(m)
    longitud_original = len(bits_mensaje)

    datos = list(bits_mensaje)
    resto = len(datos) % m
    if resto != 0:
        # Completa el último bloque con ceros para mantener bloques de tamaño m
        datos += [0] * (m - resto)

    trama = []
    for i in range(0, len(datos), m):
        trama.extend(codificar_bloque(datos[i:i + m], m, r))

    return trama, r, longitud_original
