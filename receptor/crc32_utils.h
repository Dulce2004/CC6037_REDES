#ifndef CRC32_UTILS_H
#define CRC32_UTILS_H

#include <cstdint>
#include <vector>

// Convierte una secuencia de bits en bytes para poder calcular el CRC
std::vector<uint8_t> bitsABytes(const std::vector<int>& bits);

// Calcula el CRC-32 de los datos usando el polinomio 0xEDB88320,
// con inicialización y xorout en 0xFFFFFFFF y reflexión de entrada/salida
uint32_t crc32(const std::vector<uint8_t>& datos);

// Recalcula el CRC sobre los datos originales y lo compara con el valor
// recibido al final de la trama para verificar la integridad del mensaje
bool verificarIntegridad(const std::vector<int>& trama, int longitudDatos);

#endif
