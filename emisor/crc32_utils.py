import zlib


def bits_a_bytes(bits):
    """Convierte una secuencia de bits en una secuencia de bytes"""
    bits = list(bits)
    resto = len(bits) % 8
    if resto != 0:
        # Completa con ceros a la izquierda para formar bytes completos
        bits += [0] * (8 - resto)

    salida = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        # Reconstruye cada byte a partir de los 8 bits correspondientes
        for bit in bits[i:i + 8]:
            byte = (byte << 1) | bit
        salida.append(byte)
    return bytes(salida)


def entero_a_bits(valor, cantidad_bits):
    """Convierte un entero a una lista de bits de tamaño fijo"""
    return [(valor >> (cantidad_bits - 1 - i)) & 1 for i in range(cantidad_bits)]


def calcular_integridad(bits_mensaje):
    """Concatena al mensaje los 32 bits del CRC-32 calculado."""
    # Calcula el CRC-32 del mensaje representado como bytes
    crc = zlib.crc32(bits_a_bytes(bits_mensaje)) & 0xFFFFFFFF
    # Agrega los bits del CRC al final del mensaje original
    return list(bits_mensaje) + entero_a_bits(crc, 32)
